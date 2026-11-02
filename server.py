#!/usr/bin/env python3
"""DeskStats - a tiny, pretty Mac stats page for a spare phone.

    python3 server.py              # http://<mac-name>.local:7777
    PORT=8080 python3 server.py    # different port

Requires: psutil (python3 -m pip install --user psutil)
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover
    raise SystemExit("Missing dependency. Run: python3 -m pip install --user psutil")

PORT = int(os.environ.get("PORT", "7777"))
ROOT = Path(__file__).resolve().parent
REFRESH = 1.0
DICTATE_HELPER = ROOT / "bin" / "dictate"
SUBMIT_DELAY_MS = 400

# Dictation state is read from CoreAudio, not from a key tap: while macOS
# Dictation is listening the input device is in use, which any process can
# query without Accessibility or Input Monitoring permission. That makes the
# Mac's own ⌥Z and the phone's tap produce exactly the same, observable state.
MIC_HELPER = ROOT / "bin" / "micactive"
MIC_POLL = 0.4            # seconds between CoreAudio probes
MIC_END_GRACE = 1.0       # bridge brief gaps so the UI doesn't flicker
MIC_AWAIT_TIMEOUT = 4.0   # how long to wait for dictation to really start/stop
PHONE_TAP_WINDOW = 3.0    # a start this soon after a tap is attributed to the phone

# CoreAudio lags real dictation by ~2.5s on stop (the recogniser keeps the
# device open while it finalises). DictationIM's own log line lands ~60ms after
# the keystroke, so the log is the fast signal and CoreAudio is the fallback /
# sanity check that keeps working even if Apple renames a log string.
DICTATION_LOG = "/usr/bin/log"
DICTATION_PREDICATE = 'process == "DictationIM"'
STOP_MARKERS = ("Keyboard Dictation: pause", "dictationConnectionDidPauseRecognition")
START_MARKERS = ("showCursorAccessories",)

TYPE_HELPER = ROOT / "bin" / "typephrase"
PHRASE_FILE = ROOT / "phrase.txt"
TYPE_DELAY_MS = 25
TYPE_MAX_CHARS = 1000

_type_lock = threading.Lock()

_mic_lock = threading.Lock()
_mic_active = False
_mic_changed = 0.0
_mic_source = "phone"   # "phone" or "keyboard"
_phone_tap_at = 0.0     # last time the phone asked for a dictation toggle
_mic_diag = {"live": None, "polls": 0}
_mic_last_live = 0.0    # last sampled time the mic was open
_mic_ignore_live = False  # a log "stop" landed but the device is still closing

_lock = threading.Lock()
_snapshot: dict = {}


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def sampler() -> None:
    """Sample the machine once a second into a shared dict."""
    psutil.cpu_percent(interval=None, percpu=True)  # prime the counters
    prev_net = psutil.net_io_counters()
    prev_disk = psutil.disk_io_counters()
    prev_at = time.time()

    while True:
        time.sleep(REFRESH)
        now = time.time()
        dt = max(now - prev_at, 0.001)

        per_core = psutil.cpu_percent(interval=None, percpu=True)
        cpu = sum(per_core) / len(per_core) if per_core else 0.0

        mem = psutil.virtual_memory()
        swap = _safe(psutil.swap_memory)
        disk = psutil.disk_usage(str(Path.home()))

        net = psutil.net_io_counters()
        dio = _safe(psutil.disk_io_counters)
        down = (net.bytes_recv - prev_net.bytes_recv) / dt if net else 0.0
        up = (net.bytes_sent - prev_net.bytes_sent) / dt if net else 0.0
        read = (dio.read_bytes - prev_disk.read_bytes) / dt if dio and prev_disk else 0.0
        write = (dio.write_bytes - prev_disk.write_bytes) / dt if dio and prev_disk else 0.0

        procs = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                procs.append(
                    {
                        "name": p.info["name"] or "?",
                        "cpu": p.cpu_percent(None),
                        "mem": p.memory_percent(),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        procs.sort(key=lambda p: (p["cpu"], p["mem"]), reverse=True)

        battery = _safe(psutil.sensors_battery)
        snap = {
            "host": socket.gethostname().split(".")[0],
            "time": now,
            "uptime": max(now - psutil.boot_time(), 0.0),
            "cpu": round(cpu, 1),
            "cores": len(per_core),
            "per_core": [round(c, 1) for c in per_core],
            "mem": {"percent": mem.percent, "used": mem.used, "total": mem.total},
            "swap": {"percent": swap.percent if swap else 0.0},
            "disk": {
                "percent": disk.percent,
                "used": disk.used,
                "free": disk.free,
                "total": disk.total,
            },
            "net": {"down": down, "up": up},
            "disk_io": {"read": read, "write": write},
            "load": list(_safe(os.getloadavg, (0.0, 0.0, 0.0))),
            "battery": (
                {
                    "percent": round(battery.percent, 1),
                    "plugged": bool(battery.power_plugged),
                }
                if battery
                else None
            ),
            "procs": [
                {
                    "name": p["name"][:22],
                    "cpu": round(p["cpu"], 1),
                    "mem": round(p["mem"], 1),
                }
                for p in procs[:6]
            ],
        }

        with _lock:
            _snapshot.clear()
            _snapshot.update(snap)

        prev_net, prev_disk, prev_at = net, dio, now


def _mic_state() -> dict:
    """Return mic state dict for the phone."""
    with _mic_lock:
        return {
            "active": _mic_active,
            "changed": _mic_changed,
            "source": _mic_source,
            "live": _mic_diag["live"],
            "polls": _mic_diag["polls"],
        }


def _mic_is_live():
    """Ask CoreAudio whether any input device is recording. None = unknown."""
    try:
        out = subprocess.run(
            [str(MIC_HELPER)], capture_output=True, text=True, timeout=2
        )
        if out.returncode == 0:
            return out.stdout.strip() == "1"
    except Exception:
        pass
    return None


def _apply_mic_sample(live, now: float) -> None:
    """Fold one CoreAudio sample into the shared state.

    Split out of the poll loop so the transition/attribution rules can be
    tested without hardware.
    """
    global _mic_active, _mic_changed, _mic_source, _mic_last_live, _mic_ignore_live

    if live:
        _mic_last_live = now

    with _mic_lock:
        _mic_diag["live"] = live
        _mic_diag["polls"] += 1

        if live is False:
            # The device really closed, so a pending log-stop is settled.
            _mic_ignore_live = False

        if live and not _mic_active and not _mic_ignore_live:
            _mic_active = True
            _mic_changed = now
            recent_tap = now - _phone_tap_at < PHONE_TAP_WINDOW
            _mic_source = "phone" if recent_tap else "keyboard"
            print(
                "mic live  %s source=%s" % (time.strftime("%H:%M:%S"), _mic_source),
                flush=True,
            )
        elif live is False and _mic_active and now - _mic_last_live > MIC_END_GRACE:
            _mic_active = False
            _mic_changed = now
            print("mic idle  %s (coreaudio)" % time.strftime("%H:%M:%S"), flush=True)


def _mic_force(active: bool, why: str) -> None:
    """Set state from an external signal (the DictationIM log).

    A stop keeps suppressing CoreAudio until the device actually closes,
    otherwise the ~2.5s finalise tail would resurrect the red dot.
    """
    global _mic_active, _mic_changed, _mic_source, _mic_ignore_live

    now = time.time()
    with _mic_lock:
        if active:
            _mic_ignore_live = False
        else:
            _mic_ignore_live = True
        if _mic_active == active:
            return
        _mic_active = active
        _mic_changed = now
        if active and now - _phone_tap_at >= PHONE_TAP_WINDOW:
            _mic_source = "keyboard"
        print(
            "mic %s  %s (%s)" % ("live" if active else "idle",
                                 time.strftime("%H:%M:%S"), why),
            flush=True,
        )


def _dictation_log_watcher() -> None:
    """Tail DictationIM's log: pause/stop lands ~60ms after the keystroke."""
    cmd = [DICTATION_LOG, "stream", "--style", "compact",
           "--predicate", DICTATION_PREDICATE]
    while True:
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
            )
        except Exception as exc:
            print("dictation log watcher disabled: %s" % exc, flush=True)
            return
        print("dictation log watcher running (pid %d)" % proc.pid, flush=True)
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                if any(m in line for m in STOP_MARKERS):
                    _mic_force(False, "log-stop")
                elif any(m in line for m in START_MARKERS):
                    _mic_force(True, "log-start")
        except Exception:
            pass
        proc.wait()
        print("dictation log watcher exited, restarting in 5s…", flush=True)
        time.sleep(5)


def _mic_watcher() -> None:
    """Single source of truth: mirror what CoreAudio says into _mic_*.

    macOS Dictation holds the input device open while it listens, so this
    catches a start/stop no matter who triggered it — the phone tap or ⌥Z on
    the Mac keyboard.
    """
    if not MIC_HELPER.exists():
        print("bin/micactive not found — external dictation sync disabled", flush=True)
        return

    while True:
        _apply_mic_sample(_mic_is_live(), time.time())
        time.sleep(MIC_POLL)


def _await_mic(target: bool) -> bool:
    """Wait (briefly) for the real dictation state to reach *target*."""
    deadline = time.time() + MIC_AWAIT_TIMEOUT
    while time.time() < deadline:
        if _mic_state()["active"] is target:
            return target
        time.sleep(0.12)
    return _mic_state()["active"]


def _dictation_enabled():
    """Best-effort read of the macOS Dictation toggle. None means unknown."""
    checks = (
        ("com.apple.speech.recognition.AppleSpeechRecognition.prefs",
         "DictationIMMasterDictationEnabled"),
        ("com.apple.assistant.support", "Dictation Enabled"),
    )
    for domain, key in checks:
        try:
            out = subprocess.run(
                ["defaults", "read", domain, key],
                capture_output=True, text=True, timeout=2,
            )
            if out.returncode == 0:
                return out.stdout.strip().lower() in ("1", "true", "yes")
        except Exception:
            continue
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "DeskStats"

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        global _phone_tap_at

        path, _, query = self.path.partition("?")

        if path == "/api/phrase":
            self._handle_phrase("dry=1" in query)
            return

        if path != "/api/dictate":
            self.send_error(404)
            return

        if not DICTATE_HELPER.exists():
            self._json({"ok": False, "error": "missing-helper"}, 500)
            return

        stopping = _mic_state()["active"]

        # Only submit on the stop half of the toggle, and only if asked.
        submit = stopping and "enter=1" in query

        print(
            "dictate %s query=%r stopping=%s submit=%s"
            % (time.strftime("%H:%M:%S"), query, stopping, submit),
            flush=True,
        )

        cmd = [str(DICTATE_HELPER)]
        if submit:
            cmd += ["--enter", "--delay", str(SUBMIT_DELAY_MS)]

        _phone_tap_at = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except Exception as exc:
            self._json({"ok": False, "error": "failed", "detail": str(exc)}, 500)
            return

        if proc.returncode == 2:
            self._json({"ok": False, "error": "accessibility"}, 403)
            return
        if proc.returncode != 0:
            self._json(
                {"ok": False, "error": "failed", "detail": proc.stderr.strip()}, 500
            )
            return

        # Trust the hardware, not our own guess: wait for the mic to actually
        # open or close, so the phone shows what the Mac is really doing.
        active = _await_mic(not stopping)

        self._json(
            {
                "ok": True,
                "active": active,
                "submitted": submit,
                "dictation": _dictation_enabled(),
            }
        )
        print(
            "dictate -> ok active=%s submitted=%s" % (active, submit), flush=True
        )

    def _handle_phrase(self, dry_run: bool = False) -> None:
        """Type phrase.txt into the frontmost app, then press Return."""
        if not TYPE_HELPER.exists():
            self._json({"ok": False, "error": "missing-helper"}, 500)
            return
        if not PHRASE_FILE.exists():
            self._json(
                {"ok": False, "error": "missing-file", "path": str(PHRASE_FILE)}, 404
            )
            return

        try:
            size = PHRASE_FILE.stat().st_size
        except OSError:
            size = 0
        if size > TYPE_MAX_CHARS:
            self._json(
                {"ok": False, "error": "too-long", "limit": TYPE_MAX_CHARS}, 400
            )
            return

        if not _type_lock.acquire(blocking=False):
            self._json({"ok": False, "error": "busy"}, 409)
            return

        try:
            proc = subprocess.run(
                [str(TYPE_HELPER), str(PHRASE_FILE), "--delay", str(TYPE_DELAY_MS)]
                + (["--dry-run"] if dry_run else []),
                capture_output=True,
                text=True,
                timeout=15 + TYPE_MAX_CHARS * 0.12,
            )
        except subprocess.TimeoutExpired:
            self._json({"ok": False, "error": "timeout"}, 504)
            return
        except Exception as exc:
            self._json({"ok": False, "error": "failed", "detail": str(exc)}, 500)
            return
        finally:
            _type_lock.release()

        print(
            "phrase %s exit=%s out=%r"
            % (time.strftime("%H:%M:%S"), proc.returncode, proc.stdout.strip()),
            flush=True,
        )

        if proc.returncode == 2:
            self._json({"ok": False, "error": "accessibility"}, 403)
            return
        if proc.returncode == 3:
            self._json({"ok": False, "error": "empty-file"}, 400)
            return
        if proc.returncode == 66:
            self._json({"ok": False, "error": "unreadable"}, 500)
            return
        if proc.returncode != 0:
            self._json(
                {"ok": False, "error": "failed", "detail": proc.stderr.strip()}, 500
            )
            return

        try:
            chars = int(proc.stdout.strip())
        except ValueError:
            chars = 0

        self._json({"ok": True, "chars": chars})

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]

        if path == "/api/mic":
            self._json(_mic_state())
            return

        if path.startswith("/api/stats"):
            with _lock:
                body = json.dumps(_snapshot).encode()
            ctype = "application/json"
        elif path in ("/", "/index.html"):
            body = (ROOT / "index.html").read_bytes()
            ctype = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # keep the terminal quiet
        pass


def _start_keywatcher() -> None:
    """Deprecated: the key tap was replaced by CoreAudio mic-state polling.

    Kept only so old LaunchAgents that still reference it fail loudly instead
    of silently doing nothing.
    """
    print(
        "keywatcher is no longer used — dictation is tracked via bin/micactive",
        flush=True,
    )


def main() -> None:
    threading.Thread(target=sampler, daemon=True).start()
    threading.Thread(target=_mic_watcher, daemon=True).start()
    threading.Thread(target=_dictation_log_watcher, daemon=True).start()
    time.sleep(1.2)  # let the first sample land

    host = socket.gethostname().split(".")[0]
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"\n  DeskStats running\n", flush=True)
    print(f"  on this Mac   http://localhost:{PORT}", flush=True)
    print(f"  on your phone http://{host}.local:{PORT}", flush=True)
    print(f"\n  Ctrl+C to stop.\n", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("bye")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

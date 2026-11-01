#!/usr/bin/env python3
"""DeskNook — a tiny, pretty dashboard for a spare phone on your desk.

Serves one HTML page and a small JSON API: live Mac/PC stats, a clock, a
dictation toggle, and a slide-to-type phrase button.

    python3 server.py                  # http://<hostname>.local:7777
    PORT=8080 python3 server.py        # different port
    HOST=127.0.0.1 python3 server.py   # loopback only

Requires: psutil (python3 -m pip install --user psutil)

The dictation and phrase buttons need the helper binaries in bin/ and
macOS Accessibility permission. Without them everything else still works.
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
HOST = os.environ.get("HOST", "0.0.0.0")
ROOT = Path(__file__).resolve().parent
REFRESH = 1.0
DICTATE_HELPER = ROOT / "bin" / "dictate"
MIC_IDLE_TIMEOUT = 35.0
SUBMIT_DELAY_MS = 400

TYPE_HELPER = ROOT / "bin" / "typephrase"
PHRASE_FILE = Path(os.environ.get("PHRASE_FILE", ROOT / "phrase.txt"))
TYPE_DELAY_MS = 25
TYPE_MAX_CHARS = 1000

_type_lock = threading.Lock()

_mic_lock = threading.Lock()
_mic_active = False
_mic_changed = 0.0

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


def _mic_state() -> bool:
    """Whether the phone believes dictation is listening."""
    global _mic_active
    with _mic_lock:
        if _mic_active and time.time() - _mic_changed > MIC_IDLE_TIMEOUT:
            _mic_active = False
        return _mic_active


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
    server_version = "DeskNook"

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        global _mic_active, _mic_changed

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

        with _mic_lock:
            stopping = _mic_active

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

        with _mic_lock:
            _mic_active = not _mic_active
            _mic_changed = time.time()
            active = _mic_active

        self._json(
            {
                "ok": True,
                "active": active,
                "submitted": submit,
                "dictation": _dictation_enabled(),
            }
        )
        print("dictate -> ok active=%s submitted=%s" % (active, submit), flush=True)

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
            self._json({"active": _mic_state()})
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


def main() -> None:
    threading.Thread(target=sampler, daemon=True).start()
    time.sleep(1.2)  # let the first sample land

    name = socket.gethostname().split(".")[0]
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("\n  DeskNook running\n")
    print(f"  on this machine   http://localhost:{PORT}")
    if HOST == "0.0.0.0":
        print(f"  on your phone     http://{name}.local:{PORT}")
        print("\n  Reachable from your whole local network. See SECURITY.md.")
    else:
        print(f"  bound to {HOST} only")
    print("\n  Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("bye")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Unit tests for the mic-state rules in server.py (no audio hardware needed).

    python3 tests/test_mic_state.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server as S  # noqa: E402


def reset(phone_tap_at=0.0):
    with S._mic_lock:
        S._mic_active = False
        S._mic_changed = 0.0
        S._mic_source = "phone"
    S._phone_tap_at = phone_tap_at
    S._mic_last_live = 0.0
    S._mic_ignore_live = False


def state():
    st = S._mic_state()
    return st["active"], st["source"]


def test_phone_start_is_attributed_to_phone():
    now = 1000.0
    reset(phone_tap_at=now - 0.5)          # phone tapped half a second ago
    S._apply_mic_sample(True, now)
    assert state() == (True, "phone"), state()


def test_keyboard_start_is_attributed_to_keyboard():
    now = 1000.0
    reset(phone_tap_at=now - 60)           # nobody touched the phone
    S._apply_mic_sample(True, now)
    assert state() == (True, "keyboard"), state()


def test_change_timestamp_moves_on_transition():
    reset()
    S._apply_mic_sample(True, 500.0)
    first = S._mic_state()["changed"]
    assert first == 500.0
    S._apply_mic_sample(True, 500.4)       # still live, no new change
    assert S._mic_state()["changed"] == first


def test_short_gap_does_not_flicker_off():
    reset()
    S._apply_mic_sample(True, 100.0)
    S._apply_mic_sample(False, 100.5)      # 0.5s < MIC_END_GRACE
    assert state()[0] is True, "brief gap should keep the UI on"
    S._apply_mic_sample(True, 101.0)
    assert state()[0] is True
    S._apply_mic_sample(False, 101.5)
    assert state()[0] is True


def test_long_silence_ends_the_session():
    reset()
    S._apply_mic_sample(True, 200.0)
    S._apply_mic_sample(False, 200.3)
    S._apply_mic_sample(False, 201.5)      # 1.5s since last live > grace
    assert state()[0] is False, "dictation ended — UI must follow"


def test_unknown_sample_never_changes_state():
    reset()
    S._apply_mic_sample(None, 300.0)
    assert state()[0] is False
    S._apply_mic_sample(True, 300.4)
    S._apply_mic_sample(None, 300.8)       # probe failed, keep last truth
    assert state()[0] is True


def test_await_mic_returns_promptly_when_already_there():
    reset()
    S._apply_mic_sample(False, 400.0)
    t0 = time.time()
    assert S._await_mic(False) is False
    assert time.time() - t0 < 0.5


def test_log_stop_beats_the_coreaudio_tail():
    """A stop from the log must win even though the device stays open ~2.5s."""
    reset()
    S._apply_mic_sample(True, 500.0)
    assert state()[0] is True

    S._mic_force(False, "log-stop")          # keystroke moment
    assert state()[0] is False, "UI must go grey immediately"

    for t in (500.4, 501.0, 502.0, 503.0):   # recogniser still holds the device
        S._apply_mic_sample(True, t)
        assert state()[0] is False, "CoreAudio tail must not resurrect the dot"

    S._apply_mic_sample(False, 503.5)        # device finally closes
    assert state()[0] is False

    S._apply_mic_sample(True, 510.0)         # a genuinely new session
    assert state()[0] is True, "next dictation must still light up"


def test_log_start_lights_the_dot_before_coreaudio_sees_it():
    reset(phone_tap_at=0.0)
    S._mic_force(True, "log-start")
    assert state() == (True, "keyboard"), state()


def test_log_start_from_phone_tap_keeps_phone_source():
    now = time.time()
    reset(phone_tap_at=now - 0.3)
    S._mic_force(True, "log-start")
    assert state() == (True, "phone"), state()


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok   %s" % name)
            except AssertionError as exc:
                failures += 1
                print("FAIL %s: %s" % (name, exc))
    print("\n%d failure(s)" % failures)
    sys.exit(1 if failures else 0)

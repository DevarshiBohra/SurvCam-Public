"""
recording_scheduler.py

Single source of truth for WHEN cameras should record.

Two modes:
  ALWAYS_ON   — office hours (Mon–Sat 09:00–20:00)
                All online cameras record continuously.
                At 20:00 exactly, all cameras stop immediately.

  MOTION      — after hours (Mon–Sat 20:00–09:00 + all day Sunday)
                Cameras are in standby by default.
                Any camera detecting motion → all online cameras
                start recording simultaneously.
                A shared 20-minute timer resets on every new motion
                event from any camera. When the timer expires with no
                further motion, all cameras stop together.
                Cameras that come back online mid-event join
                immediately, recording only for the remaining window.

Transitions:
  08:59:59 → 09:00:00  If motion-triggered recording is active, switch
                        silently to always-on without stopping/restarting.
  19:59:59 → 20:00:00  Stop all recordings immediately regardless of
                        whether a motion window is still open.
"""

import threading
import time
from datetime import datetime, time as dt_time
from database import list_cameras
from recorder import start_recording, stop_recording, get_recording_status
from recording_storage import storage_is_available

# ── Office hours ─────────────────────────────────────────────────────────────
OFFICE_OPEN  = dt_time(9, 0)
OFFICE_CLOSE = dt_time(20, 0)

# ── Motion-triggered recording window ────────────────────────────────────────
# How long after the LAST motion event (on ANY camera) before all cameras
# stop recording. Resets every time motion is detected anywhere.
MOTION_RECORD_DURATION_SECONDS = 10 * 60  # 10 minutes

# How often the scheduler loop runs.
CHECK_INTERVAL_SECONDS = 5

# ── Internal state ────────────────────────────────────────────────────────────
_state_lock  = threading.RLock()
_last_motion_at   = None   # datetime of most recent motion event, any camera
_motion_recording = False  # True when we are in an active motion window
_prev_always_on   = None   # used to detect the 20:00 boundary

_stop_event    = threading.Event()
_scheduler_thread = None


# ─────────────────────────────────────────────────────────────────────────────
# Public API — called by motion_detector.py
# ─────────────────────────────────────────────────────────────────────────────

def notify_motion_detected():
    """Call this every time motion is confirmed on ANY camera.

    Thread-safe. Updates the shared timer and starts recording on all
    currently online cameras if not already running.
    """
    with _state_lock:
        global _last_motion_at, _motion_recording
        _last_motion_at = datetime.now()

        if _is_always_on_now():
            # During office hours the always-on logic manages recordings;
            # motion notifications are irrelevant for scheduling purposes.
            return

        if not _motion_recording:
            _motion_recording = True
            print(
                f"[SCHED] Motion triggered — starting recording on all cameras "
                f"(window: {MOTION_RECORD_DURATION_SECONDS // 60} min after last motion)"
            )

        # Start any online camera that is not yet recording (covers cameras
        # that came back online mid-event and cameras that weren't running).
        _start_all_online()


# ─────────────────────────────────────────────────────────────────────────────
# Helper — mode detection
# ─────────────────────────────────────────────────────────────────────────────

def _is_always_on_now() -> bool:
    """True during office hours (Mon–Sat 09:00–20:00).
    Sunday is always motion-triggered."""
    now = datetime.now()
    if now.weekday() == 6:           # Sunday
        return False
    return OFFICE_OPEN <= now.time() < OFFICE_CLOSE


def _seconds_since_last_motion() -> float | None:
    with _state_lock:
        if _last_motion_at is None:
            return None
        return (datetime.now() - _last_motion_at).total_seconds()


# ─────────────────────────────────────────────────────────────────────────────
# Recording control helpers
# ─────────────────────────────────────────────────────────────────────────────

def _start_all_online():
    """Start recording on every camera that is currently online and not
    already recording. Must be called with _state_lock held (or at startup
    before the thread runs) — does NOT acquire the lock itself."""
    for camera in list_cameras():
        camera_id = camera["camera_id"]
        if camera.get("status") != "online":
            continue
        if get_recording_status(camera_id).get("recording", False):
            continue
        try:
            result = start_recording(camera_id)
            print(f"[SCHED] Started recording: {camera_id} ({result.get('encoder', '?')})")
        except Exception as exc:
            print(f"[SCHED] Failed to start recording for {camera_id}: {exc}")


def _stop_all():
    """Stop recording on every camera regardless of mode."""
    for camera in list_cameras():
        camera_id = camera["camera_id"]
        if not get_recording_status(camera_id).get("recording", False):
            continue
        try:
            stop_recording(camera_id)
            print(f"[SCHED] Stopped recording: {camera_id}")
        except Exception as exc:
            print(f"[SCHED] Failed to stop recording for {camera_id}: {exc}")


def _start_late_joiners():
    """In a motion window, start any newly-online camera that is not yet
    recording. Also handles cameras that recovered from an offline state
    mid-event — they join immediately without resetting the shared timer.
    """
    for camera in list_cameras():
        camera_id = camera["camera_id"]
        if camera.get("status") != "online":
            continue
        if get_recording_status(camera_id).get("recording", False):
            continue
        try:
            result = start_recording(camera_id)
            elapsed = _seconds_since_last_motion() or 0
            remaining = max(0, MOTION_RECORD_DURATION_SECONDS - elapsed)
            print(
                f"[SCHED] Late joiner started: {camera_id} "
                f"({result.get('encoder', '?')}) — "
                f"{remaining / 60:.1f} min remaining in window"
            )
        except Exception as exc:
            print(f"[SCHED] Failed to start late joiner {camera_id}: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Main scheduler loop
# ─────────────────────────────────────────────────────────────────────────────

def _scheduler_loop():
    global _motion_recording, _prev_always_on
    print("[SCHED] Recording scheduler started")

    while not _stop_event.is_set():
        try:
            always_on = _is_always_on_now()

            if not storage_is_available():
                print("[SCHED] WD recordings disk unavailable — stopping recordings")
                with _state_lock:
                    _stop_all()
                    _motion_recording = False
                    _prev_always_on = always_on
                _stop_event.wait(CHECK_INTERVAL_SECONDS)
                continue

            # ── Detect the 20:00 boundary (always-on → motion mode) ──────────
            if _prev_always_on is True and not always_on:
                print("[SCHED] 20:00 boundary — stopping all recordings immediately")
                with _state_lock:
                    _stop_all()
                    _motion_recording = False

            # ── Detect the 09:00 boundary (motion mode → always-on) ──────────
            # If cameras are already recording due to a motion window that
            # spans 09:00, do NOT stop/restart them — just let them continue
            # and the always-on logic will maintain them going forward.
            if _prev_always_on is False and always_on:
                print("[SCHED] 09:00 boundary — switching to always-on mode")
                with _state_lock:
                    _motion_recording = False  # motion window closed silently

            _prev_always_on = always_on

            if always_on:
                # ── ALWAYS-ON: ensure every online camera is recording ────────
                with _state_lock:
                    for camera in list_cameras():
                        camera_id = camera["camera_id"]
                        if camera.get("status") != "online":
                            continue
                        if not get_recording_status(camera_id).get("recording", False):
                            try:
                                result = start_recording(camera_id)
                                print(
                                    f"[SCHED] Always-on started: {camera_id} "
                                    f"({result.get('encoder', '?')})"
                                )
                            except Exception as exc:
                                print(f"[SCHED] Always-on start failed {camera_id}: {exc}")

                    # Stop recording for cameras that went offline
                    for camera in list_cameras():
                        camera_id = camera["camera_id"]
                        if camera.get("status") == "offline":
                            if get_recording_status(camera_id).get("recording", False):
                                try:
                                    stop_recording(camera_id)
                                    print(f"[SCHED] Always-on stopped offline: {camera_id}")
                                except Exception as exc:
                                    print(f"[SCHED] Always-on stop failed {camera_id}: {exc}")

            else:
                # ── MOTION-TRIGGERED: manage the shared timer ─────────────────
                with _state_lock:
                    if _motion_recording:
                        elapsed = _seconds_since_last_motion()

                        if elapsed is not None and elapsed >= MOTION_RECORD_DURATION_SECONDS:
                            # Timer expired — stop all cameras
                            print(
                                f"[SCHED] Motion window expired "
                                f"({MOTION_RECORD_DURATION_SECONDS // 60} min since last motion) "
                                f"— stopping all recordings"
                            )
                            _stop_all()
                            _motion_recording = False
                        else:
                            # Window still active — start any late joiners
                            _start_late_joiners()

                    else:
                        # Standby: make sure nothing is recording
                        for camera in list_cameras():
                            camera_id = camera["camera_id"]
                            if get_recording_status(camera_id).get("recording", False):
                                try:
                                    stop_recording(camera_id)
                                    print(f"[SCHED] Standby — stopped stale recording: {camera_id}")
                                except Exception as exc:
                                    print(f"[SCHED] Standby stop failed {camera_id}: {exc}")

        except Exception as exc:
            print(f"[SCHED] Scheduler loop error: {exc}")

        _stop_event.wait(CHECK_INTERVAL_SECONDS)

    print("[SCHED] Recording scheduler stopped")


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def start_recording_scheduler():
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _stop_event.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        daemon=True,
        name="recording-scheduler",
    )
    _scheduler_thread.start()


def stop_recording_scheduler():
    _stop_event.set()
    _stop_all()


def get_scheduler_status():
    always_on = _is_always_on_now()
    with _state_lock:
        motion_active = _motion_recording
        last_motion   = _last_motion_at.isoformat() if _last_motion_at else None
        elapsed       = _seconds_since_last_motion()
        remaining     = None
        if motion_active and elapsed is not None:
            remaining = max(0, MOTION_RECORD_DURATION_SECONDS - elapsed)

    return {
        "mode":              "always_on" if always_on else "motion_triggered",
        "motion_recording":  motion_active,
        "last_motion_at":    last_motion,
        "seconds_since_motion": round(elapsed, 1) if elapsed is not None else None,
        "window_remaining_seconds": round(remaining, 1) if remaining is not None else None,
        "office_hours": {
            "open":  "09:00",
            "close": "20:00",
            "days":  "Monday–Saturday",
            "sunday": "motion_triggered_all_day",
        },
        "motion_window_minutes": MOTION_RECORD_DURATION_SECONDS // 60,
    }
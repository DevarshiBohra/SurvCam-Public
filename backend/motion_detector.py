import os
import threading
import time
from datetime import datetime, time as dt_time, timedelta

import cv2
import numpy as np

from database import list_cameras, add_motion_event, update_motion_event, list_motion_events
from recording_storage import SNAPSHOT_ROOT
from stream import get_latest_frame_bytes, ensure_camera_stream
from motion_notifier import notify_motion


CHECK_INTERVAL_SECONDS = 1
MOTION_END_DELAY_SECONDS = 5
MOTION_COOLDOWN_SECONDS = 30

OFFICE_OPEN = dt_time(9, 0)
OFFICE_CLOSE = dt_time(20, 0)

MOTION_DELTA_THRESHOLD = 30
MOTION_MIN_CONTOUR_AREA = 400
DEFAULT_MIN_CHANGED_PIXELS = 5000
PER_CAMERA_MIN_CHANGED_PIXELS = {
    camera_id.strip(): int(pixel_count)
    for item in os.environ.get("SURVCAM_PER_CAMERA_MIN_CHANGED_PIXELS", "").split(",")
    if ":" in item
    for camera_id, pixel_count in [item.split(":", 1)]
    if camera_id.strip() and pixel_count.strip().isdigit()
}
MOTION_BOTTOM_HALF_CAMERA_IDS = {
    camera_id.strip()
    for camera_id in os.environ.get("SURVCAM_MOTION_BOTTOM_HALF_CAMERA_IDS", "").split(",")
    if camera_id.strip()
}

MIN_CHANGED_PIXELS = DEFAULT_MIN_CHANGED_PIXELS
#MOTION_IGNORED_CAMERA_IDS = {"cam005"}
MOTION_IGNORED_CAMERA_IDS = {}

_motion_thread = None
_stop_event = threading.Event()
_camera_states = {}


def is_motion_detection_active_now():
    now = datetime.now()
    weekday = now.weekday()  # Monday=0, Sunday=6
    current_time = now.time()

    if weekday == 6:
        return True

    if OFFICE_OPEN <= current_time < OFFICE_CLOSE:
        return False

    return True


def _decode_jpeg(frame_bytes):
    arr = np.frombuffer(frame_bytes, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return frame


def _prepare_frame(frame, camera_id):
    if camera_id in MOTION_BOTTOM_HALF_CAMERA_IDS:
        frame = frame[frame.shape[0] // 2 :, :]

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    target_height = 120 if camera_id in MOTION_BOTTOM_HALF_CAMERA_IDS else 240
    gray = cv2.resize(gray, (320, target_height))
    gray = cv2.GaussianBlur(gray, (21, 21), 0)
    return gray


def _save_snapshot(camera_id, frame):
    now = datetime.now()
    day_dir = SNAPSHOT_ROOT / camera_id / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)

    filename = now.strftime("%H-%M-%S") + ".jpg"
    path = day_dir / filename

    cv2.imwrite(str(path), frame)
    return str(path)


def _get_state(camera_id):
    if camera_id not in _camera_states:
        _camera_states[camera_id] = {
            "previous_frame": None,
            "motion_active": False,
            "motion_event_id": None,
            "motion_started_at": None,
            "last_motion_at": None,
            "cooldown_until": None,
            "last_error": None,
        }

    return _camera_states[camera_id]


def _get_motion_threshold(camera_id):
    return PER_CAMERA_MIN_CHANGED_PIXELS.get(str(camera_id), DEFAULT_MIN_CHANGED_PIXELS)


def _build_motion_mask(previous_frame, current_frame):
    delta = cv2.absdiff(previous_frame, current_frame)
    _, mask = cv2.threshold(delta, MOTION_DELTA_THRESHOLD, 255, cv2.THRESH_BINARY)
    mask = cv2.medianBlur(mask, 5)

    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.dilate(mask, kernel, iterations=2)

    return mask


def _process_camera(camera):
    camera_id = camera["camera_id"]

    if camera_id in MOTION_IGNORED_CAMERA_IDS:
        _camera_states.pop(camera_id, None)
        return

    camera_name = camera.get("name") or camera_id

    if camera.get("status") != "online":
        return

    ensure_camera_stream(camera_id)

    state = _get_state(camera_id)

    frame_bytes = get_latest_frame_bytes(camera_id)
    if not frame_bytes:
        return

    frame = _decode_jpeg(frame_bytes)
    if frame is None:
        return

    prepared = _prepare_frame(frame, camera_id)

    if state["previous_frame"] is None:
        state["previous_frame"] = prepared
        return

    motion_mask = _build_motion_mask(state["previous_frame"], prepared)

    contours, _ = cv2.findContours(motion_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    changed_pixels = 0

    for contour in contours:
        if cv2.contourArea(contour) >= MOTION_MIN_CONTOUR_AREA:
            changed_pixels += cv2.contourArea(contour)

    now = datetime.now()

    cooldown_until = state.get("cooldown_until")
    in_cooldown = cooldown_until is not None and now < cooldown_until

    motion_threshold = _get_motion_threshold(camera_id)

    if changed_pixels > motion_threshold and not in_cooldown:
        state["last_motion_at"] = now

        if not state["motion_active"]:
            snapshot_path = _save_snapshot(camera_id, frame)
            event_id = add_motion_event(
                camera_id=camera_id,
                started_at=now.isoformat(),
                snapshot_path=snapshot_path
            )

            state["motion_active"] = True
            state["motion_event_id"] = event_id
            state["motion_started_at"] = now

            print(f"Motion started: {camera_id}, changed_pixels={changed_pixels}")

        # Notify the recording scheduler so all cameras start recording.
        # This is the cross-camera trigger: one camera detects motion →
        # all cameras record. The scheduler owns debouncing and the shared
        # 20-minute timer; calling this on every detected frame is safe.
        try:
            from recording_scheduler import notify_motion_detected
            notify_motion_detected()
        except Exception as exc:
            print(f"Scheduler notify error for {camera_id}: {exc}")

        # Push notification (independent 10-min cooldown, not tied to
        # the recording timer above).
        notify_motion(camera_id, camera_name)

    if state["motion_active"]:
        last_motion_at = state["last_motion_at"]

        if last_motion_at and (now - last_motion_at).total_seconds() >= MOTION_END_DELAY_SECONDS:
            started_at = state["motion_started_at"]
            duration = int((now - started_at).total_seconds())

            update_motion_event(
                event_id=state["motion_event_id"],
                ended_at=now.isoformat(),
                duration_seconds=duration
            )

            print(f"Motion ended: {camera_id}, duration={duration}s")

            state["motion_active"] = False
            state["motion_event_id"] = None
            state["motion_started_at"] = None
            state["last_motion_at"] = None
            state["cooldown_until"] = now + timedelta(seconds=MOTION_COOLDOWN_SECONDS)

    state["previous_frame"] = prepared


def _motion_loop():
    print("Motion detector started")

    detection_was_active = False  # track previous state

    while not _stop_event.is_set():
        try:
            active = is_motion_detection_active_now()

            # If not active, sleep and remember that we are inactive
            if not active:
                detection_was_active = False
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue

            # Transition from inactive to active → clear baselines
            if not detection_was_active:
                print("[MOTION] Motion detection activated — resetting camera baselines")
                _camera_states.clear()   # reset all stored previous frames
                detection_was_active = True

            cameras = list_cameras()

            for camera in cameras:
                try:
                    _process_camera(camera)
                except Exception as exc:
                    camera_id = camera.get("camera_id", "unknown")
                    _get_state(camera_id)["last_error"] = str(exc)
                    print(f"Motion detector error for {camera_id}: {exc}")

        except Exception as exc:
            print(f"Motion detector loop error: {exc}")

        time.sleep(CHECK_INTERVAL_SECONDS)

    print("Motion detector stopped")


def start_motion_detector():
    global _motion_thread

    if _motion_thread and _motion_thread.is_alive():
        return

    _stop_event.clear()
    _motion_thread = threading.Thread(target=_motion_loop, daemon=True)
    _motion_thread.start()


def stop_motion_detector():
    _stop_event.set()


def get_motion_status():
    now = datetime.now()

    return {
        "enabled": True,
        "active_now": is_motion_detection_active_now(),
        "time": now.isoformat(),
        "office_hours": {
            "days": "Monday-Saturday",
            "open": "09:00",
            "close": "20:00",
            "sunday": "closed_all_day",
            "timezone": "Asia/Kolkata"
        },
        "settings": {
            "check_interval_seconds": CHECK_INTERVAL_SECONDS,
            "motion_end_delay_seconds": MOTION_END_DELAY_SECONDS,
            "motion_cooldown_seconds": MOTION_COOLDOWN_SECONDS,
            "min_changed_pixels": MIN_CHANGED_PIXELS
        },
        "cameras": _camera_states
    }


def get_motion_events(camera_id=None):
    return list_motion_events(camera_id=camera_id)
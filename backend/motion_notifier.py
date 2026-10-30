from datetime import datetime, timedelta

from notifications import send_notification

MOTION_COOLDOWN_SECONDS = 600  # 10 minutes

MOTION_IGNORED_CAMERA_IDS = {}

_last_notified = {}


def notify_motion(camera_id: str, camera_name: str):
    """Call this from motion_detector.py whenever motion is confirmed for
    a camera during an active (after-hours) detection window. Handles its
    own per-camera 10-minute cooldown internally, so motion_detector.py
    doesn't need to track that - just call this every time motion is
    detected and it will decide whether a push is actually due.

    Respects the mute (checked inside send_notification via
    is_muted()) automatically - no need to check that here either.
    """
    if camera_id in MOTION_IGNORED_CAMERA_IDS:
        return

    now = datetime.now()
    last = _last_notified.get(camera_id)

    if last and (now - last).total_seconds() < MOTION_COOLDOWN_SECONDS:
        return

    _last_notified[camera_id] = now

    send_notification(
        notif_type="motion",
        title="Motion detected",
        body=f"Motion detected on {camera_name} at {now.strftime('%I:%M %p')}",
        camera_id=camera_id,
        data={"detected_at": now.isoformat()},
        data_only=True,  # needed so the app can attach the mute action button
    )
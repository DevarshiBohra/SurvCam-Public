import threading

from database import (
    cleanup_old_heartbeat_history,
    cleanup_old_motion_events,
    cleanup_old_notification_history,
)
from snapshot_cleanup import cleanup_old_snapshots

CLEANUP_INTERVAL_SECONDS = 24 * 60 * 60

_cleanup_thread = None
_stop_event = threading.Event()


def _cleanup_loop():
    print("Notification history cleanup started")

    while not _stop_event.is_set():
        try:
            deleted = cleanup_old_notification_history()
            if deleted:
                print(f"Notification history cleanup deleted {deleted} old notification(s)")
        except Exception as exc:
            print(f"Notification history cleanup failed: {exc}")

        try:
            deleted = cleanup_old_heartbeat_history()
            if deleted:
                print(f"Heartbeat history cleanup deleted {deleted} old row(s)")
        except Exception as exc:
            print(f"Heartbeat history cleanup failed: {exc}")

        try:
            deleted = cleanup_old_motion_events()
            if deleted:
                print(f"Motion event cleanup deleted {deleted} old event(s)")
        except Exception as exc:
            print(f"Motion event cleanup failed: {exc}")

        try:
            deleted = cleanup_old_snapshots()
            if deleted:
                print(f"Snapshot cleanup deleted {len(deleted)} old day folder(s)")
        except Exception as exc:
            print(f"Snapshot cleanup failed: {exc}")

        _stop_event.wait(CLEANUP_INTERVAL_SECONDS)

    print("Notification history cleanup stopped")


def start_notification_cleanup():
    global _cleanup_thread

    if _cleanup_thread and _cleanup_thread.is_alive():
        return

    _stop_event.clear()
    _cleanup_thread = threading.Thread(
        target=_cleanup_loop,
        daemon=True,
        name="notification-history-cleanup",
    )
    _cleanup_thread.start()


def stop_notification_cleanup():
    _stop_event.set()

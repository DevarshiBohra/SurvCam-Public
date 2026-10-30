import threading

from database import cleanup_old_notification_history

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

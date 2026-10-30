import threading

from notifications import send_notification
from recording_storage import storage_is_available


CHECK_INTERVAL_SECONDS = 5
STABLE_CHECKS_REQUIRED = 2

_stop_event = threading.Event()
_monitor_thread = None


def _notify_state_change(previous_state: bool | None, current_state: bool) -> bool:
    if previous_state is None and current_state:
        return current_state

    if previous_state is not None and previous_state == current_state:
        return current_state

    if current_state:
        send_notification(
            notif_type="external_recording_drive_mounted",
            title="External recording drive mounted",
            body="External recording drive mounted. Recordings will begin",
        )
    else:
        send_notification(
            notif_type="external_recording_drive_unmounted",
            title="External recording drive unmounted",
            body="External recording drive unmounted, recordings will be stopped",
        )

    return current_state


def _advance_candidate_state(
    previous_state: bool | None,
    candidate_state: bool | None,
    candidate_checks: int,
    current_state: bool,
):
    if current_state == previous_state:
        return previous_state, None, 0, False

    if current_state == candidate_state:
        candidate_checks += 1
    else:
        candidate_state = current_state
        candidate_checks = 1

    if candidate_checks >= STABLE_CHECKS_REQUIRED:
        return current_state, None, 0, True

    return previous_state, candidate_state, candidate_checks, False


def _monitor_loop():
    previous_state = None
    candidate_state = None
    candidate_checks = 0

    while not _stop_event.is_set():
        current_state = storage_is_available()
        previous_stable_state = previous_state
        previous_state, candidate_state, candidate_checks, confirmed = _advance_candidate_state(
            previous_state,
            candidate_state,
            candidate_checks,
            current_state,
        )

        if confirmed:
            try:
                _notify_state_change(previous_stable_state, current_state)
            except Exception as exc:
                print(f"[STORAGE] Notification failed: {exc}")

        _stop_event.wait(CHECK_INTERVAL_SECONDS)


def start_recording_storage_monitor():
    global _monitor_thread

    if _monitor_thread and _monitor_thread.is_alive():
        return

    _stop_event.clear()
    _monitor_thread = threading.Thread(
        target=_monitor_loop,
        daemon=True,
        name="recording-storage-monitor",
    )
    _monitor_thread.start()


def stop_recording_storage_monitor():
    _stop_event.set()
    if _monitor_thread and _monitor_thread.is_alive():
        _monitor_thread.join(timeout=CHECK_INTERVAL_SECONDS + 1)
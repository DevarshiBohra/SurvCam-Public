import socket
import threading
import time

from database import list_cameras, add_reachability_probe, cleanup_old_reachability_probes

# Matches the fixed stream port your ESP32-CAM firmware and stream.py both
# already assume (CAMERA stream server started on port 81).
STREAM_PORT = 81
PROBE_INTERVAL_SECONDS = 30
PROBE_TIMEOUT_SECONDS = 3

_stop_event = threading.Event()
_probe_thread = None


def _probe_once(ip_address: str, port: int, timeout: float = PROBE_TIMEOUT_SECONDS):
    """Attempts a raw TCP connect to the camera's stream port. Doesn't
    fetch any actual video - just proves whether something is listening
    and accepting connections at that IP:port right now, independent of
    MQTT entirely."""
    try:
        with socket.create_connection((ip_address, port), timeout=timeout):
            return True, None
    except Exception as exc:
        return False, str(exc)


def _probe_loop():
    print("Reachability probe started")

    while not _stop_event.is_set():
        try:
            cleanup_old_reachability_probes()

            for camera in list_cameras():
                camera_id = camera["camera_id"]
                ip_address = camera.get("ip_address")
                status = camera.get("status")

                # Only bother probing cameras the backend currently
                # believes are offline - if MQTT already says a camera is
                # online, we don't need a second opinion on it.
                if status != "offline":
                    continue

                if not ip_address:
                    continue

                reachable, error = _probe_once(ip_address, STREAM_PORT)
                add_reachability_probe(camera_id, ip_address, STREAM_PORT, reachable, error)

                if reachable:
                    print(
                        f"Reachability probe: {camera_id} at {ip_address}:{STREAM_PORT} "
                        f"is REACHABLE despite being marked offline (likely an MQTT-only issue)"
                    )

        except Exception as exc:
            print(f"Reachability probe loop error: {exc}")

        _stop_event.wait(PROBE_INTERVAL_SECONDS)

    print("Reachability probe stopped")


def start_reachability_probe():
    global _probe_thread

    if _probe_thread and _probe_thread.is_alive():
        return

    _stop_event.clear()
    _probe_thread = threading.Thread(target=_probe_loop, daemon=True)
    _probe_thread.start()


def stop_reachability_probe():
    _stop_event.set()
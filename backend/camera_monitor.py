import threading
from datetime import datetime, timedelta

from database import list_cameras, list_heartbeat_history, list_reachability_probes
from notifications import send_notification
from telemetry import first_telemetry_after

CHECK_INTERVAL_SECONDS = 15
# Camera must be continuously offline this long before we actually notify -
# gives brief blips a chance to self-resolve without spamming a push. The
# exact moment it went offline is still logged immediately regardless.
OFFLINE_NOTIFY_THRESHOLD_SECONDS = 60
# How weak (in dBm, i.e. more negative) rssi needs to have gotten compared
# to its earlier baseline to count as "a degrading signal" ahead of a drop.
RSSI_DEGRADATION_THRESHOLD = 8
# uptime_ms below this on the reconnect heartbeat is treated as proof the
# device actually rebooted, rather than just reconnecting to WiFi/MQTT.
REBOOT_UPTIME_THRESHOLD_MS = 120_000
# The monitor notices an outage some time after it really started (broker
# keepalive + check interval), so a camera whose boot time is within this many
# seconds before "offline since" still counts as rebooted during the outage.
REBOOT_DETECT_MARGIN_SECONDS = 120

_offline_since = {}
_notified_down = {}

_stop_event = threading.Event()
_monitor_thread = None


def _format_duration(delta: timedelta) -> str:
    total_minutes = int(delta.total_seconds() // 60)

    if total_minutes < 1:
        return "less than a minute"

    hours, minutes = divmod(total_minutes, 60)

    if hours == 0:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"

    return f"{hours} hour{'s' if hours != 1 else ''} {minutes} minute{'s' if minutes != 1 else ''}"


def _infer_offline_reason(camera_id: str, offline_since: datetime) -> str:
    """Best-guess reason available at the moment we decide to notify -
    before the camera has necessarily reconnected, so a reboot can't be
    confirmed yet (that only shows up in the recovery heartbeat)."""

    probes = list_reachability_probes(camera_id, hours=1)
    reachable_after_drop = [
        p for p in probes
        if p["checked_at"] >= offline_since.isoformat() and p["reachable"]
    ]

    if reachable_after_drop:
        return ("The camera appears reachable on the network despite being marked "
                "offline - this looks like an MQTT connectivity issue rather than "
                "a camera problem.")

    history = list_heartbeat_history(camera_id, hours=1)
    before_drop = [h for h in history if h["recorded_at"] < offline_since.isoformat() and h["rssi"] is not None]

    if len(before_drop) >= 4:
        recent = before_drop[-2:]
        earlier = before_drop[-4:-2]
        recent_avg = sum(h["rssi"] for h in recent) / len(recent)
        earlier_avg = sum(h["rssi"] for h in earlier) / len(earlier)

        if (earlier_avg - recent_avg) >= RSSI_DEGRADATION_THRESHOLD:
            return ("Signal strength was weakening in the minutes before it dropped - "
                    "likely a WiFi signal or interference issue.")

    return ("Camera is unreachable on the network and no signal warning was seen "
            "beforehand. Possible power interruption - will confirm once it reconnects.")


def _rebooted_during_outage(telemetry_row: dict, offline_since: datetime):
    """True / False if the first heartbeat after the outage proves a reboot did /
    did not happen during it; None if it can't be told (old firmware, bad data).

    The heartbeat carries the *previous* boot's restart cause for the whole boot,
    so we must first check that the camera really booted during this outage,
    otherwise an old cause from days ago would be reported."""
    uptime_ms = telemetry_row.get("uptime_ms")
    recorded_at = telemetry_row.get("recorded_at")

    if uptime_ms is None or not recorded_at:
        return None

    try:
        back_at = datetime.strptime(recorded_at[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

    boot_time = back_at - timedelta(milliseconds=uptime_ms)
    return boot_time >= offline_since - timedelta(seconds=REBOOT_DETECT_MARGIN_SECONDS)


def _describe_reboot(telemetry_row: dict) -> str:
    cause = telemetry_row.get("restart_cause")
    reset = telemetry_row.get("reset_reason_str")

    if cause == "wifi_lost":
        return "Camera restarted itself after losing WiFi for over a minute."
    if cause == "mqtt_lost":
        return ("Camera restarted itself after its MQTT connection stayed down "
                "while WiFi was up.")
    if cause == "wifi_connect_timeout":
        return "Camera restarted itself because it could not join WiFi after a restart."
    if cause == "camera_init_fail":
        return "Camera restarted itself because the camera sensor failed to start."
    if cause == "camera_tint":
        return "Camera restarted itself to fix a colour-tint fault."

    if reset == "brownout":
        return "Camera restarted after a power dip (brownout) - check its power supply."
    if reset == "poweron":
        return "Camera was power-cycled (power was cut) - likely a power interruption."
    if reset in ("task_wdt", "int_wdt", "wdt"):
        return "The watchdog restarted the camera after its firmware froze."
    if reset == "panic":
        return "Camera crashed and restarted (firmware panic)."

    return "Camera rebooted (uptime reset)."


def _infer_recovery_reason(camera_id: str, offline_since: datetime) -> str:
    """Runs once the camera has actually come back. Uses the new firmware's
    reset/restart-cause fields when available, and falls back to the uptime_ms
    check for cameras still running older firmware."""

    # 1) Newer firmware: exact reason.
    try:
        row = first_telemetry_after(camera_id, offline_since)
    except Exception as exc:
        print(f"Camera monitor: telemetry lookup failed for {camera_id}: {exc}")
        row = None

    if row is not None:
        rebooted = _rebooted_during_outage(row, offline_since)

        if rebooted is True:
            return _describe_reboot(row)
        if rebooted is False:
            return "Camera did not reboot - likely a temporary WiFi or network interruption."

    # 2) Older firmware / no telemetry: original uptime-based guess.
    history = list_heartbeat_history(camera_id, hours=2)
    after_drop = [h for h in history if h["recorded_at"] > offline_since.isoformat()]

    if not after_drop:
        return ""

    first_back = after_drop[0]
    uptime_ms = first_back.get("uptime_ms")

    if uptime_ms is not None and uptime_ms < REBOOT_UPTIME_THRESHOLD_MS:
        return "Camera rebooted (uptime reset) - likely a power interruption."

    return "Camera did not reboot - likely a temporary WiFi or network interruption."


def _check_cameras():
    for camera in list_cameras():
        camera_id = camera["camera_id"]
        name = camera.get("name") or camera_id
        status = camera.get("status")

        if status == "offline":
            if camera_id not in _offline_since:
                _offline_since[camera_id] = datetime.now()
                print(f"{camera_id} went offline at {_offline_since[camera_id].isoformat()}")

            elapsed = (datetime.now() - _offline_since[camera_id]).total_seconds()

            if elapsed >= OFFLINE_NOTIFY_THRESHOLD_SECONDS and not _notified_down.get(camera_id):
                offline_since = _offline_since[camera_id]
                reason = _infer_offline_reason(camera_id, offline_since)

                send_notification(
                    notif_type="camera_down",
                    title=f"{name} is offline",
                    body=f"Went offline at {offline_since.strftime('%I:%M %p')}. {reason}",
                    camera_id=camera_id,
                    data={"offline_since": offline_since.isoformat()},
                )
                _notified_down[camera_id] = True

        else:
            if camera_id in _offline_since:
                offline_since = _offline_since[camera_id]
                was_notified = _notified_down.get(camera_id, False)

                if was_notified:
                    downtime = datetime.now() - offline_since
                    confirmed_reason = _infer_recovery_reason(camera_id, offline_since)

                    send_notification(
                        notif_type="camera_recovered",
                        title=f"{name} is back online",
                        body=f"Was offline for {_format_duration(downtime)}. {confirmed_reason}",
                        camera_id=camera_id,
                        data={"downtime_seconds": int(downtime.total_seconds())},
                    )

                _offline_since.pop(camera_id, None)
                _notified_down.pop(camera_id, None)


def _monitor_loop():
    print("Camera monitor started")

    while not _stop_event.is_set():
        try:
            _check_cameras()
        except Exception as exc:
            print(f"Camera monitor loop error: {exc}")

        _stop_event.wait(CHECK_INTERVAL_SECONDS)

    print("Camera monitor stopped")


def start_camera_monitor():
    global _monitor_thread

    if _monitor_thread and _monitor_thread.is_alive():
        return

    _stop_event.clear()
    _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
    _monitor_thread.start()


def stop_camera_monitor():
    _stop_event.set()
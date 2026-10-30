from datetime import datetime, timedelta
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import hashlib
import os
import hmac
import secrets
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from recorder import start_recording, stop_recording, get_recording_status
from auto_recorder import start_auto_recorder, stop_auto_recorder, get_auto_recording_status, cleanup_old_recordings
from recording_scheduler import start_recording_scheduler, stop_recording_scheduler, get_scheduler_status
from auth import hash_password, verify_password, create_access_token, get_current_user, require_admin
from telegram_2fa import send_login_code
from recordings import list_recording_days, list_recording_hours, get_hour_manifest
from reachability_probe import start_reachability_probe, stop_reachability_probe
from notifications import init_firebase, register_token, unregister_token, mute_device_for_one_hour, mute_device_for_hours, unmute_device, get_device_mute_status, get_history
from notification_cleanup import start_notification_cleanup, stop_notification_cleanup
from camera_monitor import start_camera_monitor, stop_camera_monitor
from recording_storage import RecordingStorageUnavailable, SNAPSHOT_ROOT
from recording_storage_monitor import (
    start_recording_storage_monitor,
    stop_recording_storage_monitor,
)

from database import (
    add_user,
    get_user,
    list_users,
    set_user_active,
    init_db,
    add_camera,
    list_cameras,
    get_camera,
    update_camera_status,
    list_heartbeat_history,
    list_rssi_history,
    list_reachability_probes,
    create_login_challenge,
    get_login_challenge,
    increment_login_challenge_attempts,
    delete_login_challenge,
)

from telemetry import (
    init_telemetry_db,
    list_telemetry,
    latest_telemetry,
    latest_for_all_cameras,
    list_events,
)

from firmware import (
    OtaConfigError,
    build_command,
    delete_firmware,
    download_token,
    firmware_path,
    get_firmware,
    list_firmware,
    save_firmware,
    verify_download_token,
)

from motion_detector import (
    start_motion_detector,
    stop_motion_detector,
    get_motion_status,
    get_motion_events,
)

from mqtt_client import start_mqtt_client, get_mqtt_status
from stream import (
    MJPEG_MEDIA_TYPE,
    ensure_camera_stream,
    get_stream_status,
    mjpeg_frame_generator,
)

from playback import (
    list_recording_files,
    get_latest_recording,
    recording_file_response,
    playback_page,
)

app = FastAPI(
    title="SurvCam Backend",
    description="Backend server for ESP32-CAM surveillance system",
    version="0.5.0"
)

mqtt_client = None

# Only these accounts may update firmware and restart/roll back cameras.
# Set SURVCAM_CONTROL_USERS to a comma-separated list of camera-control usernames
# (comma-separated). The shared "admin" account is deliberately NOT in this list.
CONTROL_USERS = {
    name.strip()
    for name in os.environ.get("SURVCAM_CONTROL_USERS", "").split(",")
    if name.strip()
}


def require_control_user(current_user=Depends(get_current_user)):
    if current_user.get("username") not in CONTROL_USERS:
        raise HTTPException(status_code=403, detail="Camera control is not available for this account")
    return current_user


class CameraCreate(BaseModel):
    camera_id: str
    name: str
    password: str


class CameraStatusUpdate(BaseModel):
    status: str
    ip_address: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class VerifyTwoFactorRequest(BaseModel):
    challenge_id: str
    code: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"


class UserActiveUpdate(BaseModel):
    is_active: bool

class DeviceTokenRequest(BaseModel):
    token: str


class OtaRequest(BaseModel):
    version: str | None = None   # omit = latest uploaded firmware


@app.on_event("startup")
def startup_event():
    global mqtt_client

    init_db()
    init_telemetry_db()
    start_notification_cleanup()
    mqtt_client = start_mqtt_client()
    start_auto_recorder()
    start_recording_scheduler()
    start_motion_detector()
    start_reachability_probe()
    init_firebase()
    start_recording_storage_monitor()
    start_camera_monitor()


@app.on_event("shutdown")
def shutdown_event():
    global mqtt_client
    stop_motion_detector()
    stop_auto_recorder()
    stop_recording_scheduler()
    stop_reachability_probe()
    stop_recording_storage_monitor()
    stop_camera_monitor()

    if mqtt_client is not None:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()

    stop_notification_cleanup()


# ---------------------------------------------------------------------------
# Public routes (no auth) - safe to leave open, reveal nothing sensitive
# ---------------------------------------------------------------------------



@app.get("/")
def root():
    return {
        "status": "running",
        "service": "survcam-backend",
        "version": "0.5.0"
    }


@app.get("/health")
def health():
    mqtt_status = get_mqtt_status()

    return {
        "status": "healthy",
        "backend": "running",
        "mqtt": mqtt_status,
        "database": "ready",
        "recording": "not_configured_yet",
        "time": datetime.now().isoformat()
    }


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.get("/snapshots/{camera_id}/days")
def snapshot_days(camera_id: str, current_user=Depends(get_current_user)):
    base = SNAPSHOT_ROOT / camera_id
    if not base.exists():
        return {"camera_id": camera_id, "days": []}
    days = sorted([d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")], reverse=True)
    return {"camera_id": camera_id, "days": days}

@app.get("/snapshots/{camera_id}/days/{date}/hours")
def snapshot_hours(camera_id: str, date: str, current_user=Depends(get_current_user)):
    base = SNAPSHOT_ROOT / camera_id / date
    if not base.exists():
        return {"camera_id": camera_id, "date": date, "hours": []}
    hours_set = set()
    for f in base.iterdir():
        if f.is_file() and f.suffix.lower() == ".jpg":
            try:
                hours_set.add(int(f.name.split("-")[0]))
            except (ValueError, IndexError):
                pass
    return {"camera_id": camera_id, "date": date, "hours": sorted(hours_set)}

@app.get("/snapshots/{camera_id}/days/{date}/hours/{hour}")
def snapshot_files_for_hour(camera_id: str, date: str, hour: int, current_user=Depends(get_current_user)):
    base = SNAPSHOT_ROOT / camera_id / date
    if not base.exists():
        return {"camera_id": camera_id, "date": date, "hour": hour, "snapshots": []}
    files = sorted([f.name for f in base.iterdir()
                    if f.is_file() and f.suffix.lower() == ".jpg" and f.name.startswith(f"{hour:02d}-")])
    return {"camera_id": camera_id, "date": date, "hour": hour, "snapshots": files}

@app.get("/snapshots/{camera_id}/file/{date}/{filename}")
def serve_snapshot(camera_id: str, date: str, filename: str, current_user=Depends(get_current_user)):
    path = SNAPSHOT_ROOT / camera_id / date / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Snapshot not found")
    try:
        path.resolve().relative_to(SNAPSHOT_ROOT.resolve())  # security check
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
    return FileResponse(str(path), media_type="image/jpeg", filename=filename)

@app.get("/recordings/{camera_id}/days")
def recording_days(camera_id: str, current_user=Depends(get_current_user)):
    return {
        "camera_id": camera_id,
        "days": list_recording_days(camera_id)
    }


@app.get("/recordings/{camera_id}/days/{date}/hours")
def recording_hours(camera_id: str, date: str, current_user=Depends(get_current_user)):
    return {
        "camera_id": camera_id,
        "date": date,
        "hours": list_recording_hours(camera_id, date)
    }


@app.get("/recordings/{camera_id}/manifest/{date}/{hour}")
def recording_manifest(camera_id: str, date: str, hour: int, current_user=Depends(get_current_user)):
    return get_hour_manifest(camera_id, date, hour)

@app.get("/recordings/scheduler-status")
def scheduler_status(current_user=Depends(get_current_user)):
    return get_scheduler_status()

@app.post("/auth/login")
def login(payload: LoginRequest):
    user = get_user(payload.username)

    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if user.get("is_active") != 1:
        raise HTTPException(status_code=403, detail="User is disabled")

    if not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge_id = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()

    create_login_challenge(
        challenge_id,
        user["username"],
        code_hash,
        expires_at.isoformat(),
    )

    try:
        send_login_code(user["username"], code, expires_minutes=10)
    except RuntimeError as exc:
        delete_login_challenge(challenge_id)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "requires_2fa": True,
        "challenge_id": challenge_id,
        "expires_in": 600,
    }


@app.post("/auth/verify-2fa")
def verify_two_factor(payload: VerifyTwoFactorRequest):
    challenge = get_login_challenge(payload.challenge_id)

    if challenge is None:
        raise HTTPException(status_code=401, detail="Invalid or expired login challenge")

    if datetime.utcnow() >= datetime.fromisoformat(challenge["expires_at"]):
        delete_login_challenge(payload.challenge_id)
        raise HTTPException(status_code=401, detail="Login code expired")

    if challenge["attempts"] >= 5:
        delete_login_challenge(payload.challenge_id)
        raise HTTPException(status_code=401, detail="Too many invalid login code attempts")

    submitted_hash = hashlib.sha256(payload.code.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(submitted_hash, challenge["code_hash"]):
        increment_login_challenge_attempts(payload.challenge_id)
        raise HTTPException(status_code=401, detail="Invalid login code")

    user = get_user(challenge["username"])
    delete_login_challenge(payload.challenge_id)

    if user is None or user.get("is_active") != 1:
        raise HTTPException(status_code=401, detail="User inactive or not found")

    token = create_access_token(user["username"], user["role"])

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "username": user["username"],
            "role": user["role"]
        }
    }


@app.get("/auth/capabilities")
def auth_capabilities(current_user=Depends(get_current_user)):
    return {"camera_control": current_user["username"] in CONTROL_USERS}


@app.get("/auth/me")
def auth_me(current_user=Depends(get_current_user)):
    return {
        "username": current_user["username"],
        "role": current_user["role"],
        "is_active": current_user["is_active"]
    }


# ---------------------------------------------------------------------------
# User management (admin-only)
# ---------------------------------------------------------------------------

@app.post("/users")
def create_user(payload: UserCreate, admin=Depends(require_admin)):
    if payload.role not in ["admin", "user"]:
        raise HTTPException(status_code=400, detail="Invalid role")

    if get_user(payload.username):
        raise HTTPException(status_code=409, detail="Username already exists")

    add_user(payload.username, hash_password(payload.password), payload.role)

    return {
        "message": "user_created",
        "username": payload.username,
        "role": payload.role
    }


@app.get("/users")
def users(admin=Depends(require_admin)):
    return {
        "users": list_users()
    }


@app.patch("/users/{username}/active")
def update_user_active(username: str, payload: UserActiveUpdate, admin=Depends(require_admin)):
    updated = set_user_active(username, payload.is_active)

    if not updated:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "message": "user_active_updated",
        "username": username,
        "is_active": payload.is_active
    }


# ---------------------------------------------------------------------------
# Cameras
# ---------------------------------------------------------------------------

@app.get("/cameras")
def cameras(current_user=Depends(get_current_user)):
    return {
        "cameras": list_cameras()
    }


@app.get("/cameras/{camera_id}")
def camera_detail(camera_id: str, current_user=Depends(get_current_user)):
    camera = get_camera(camera_id)

    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    return camera


@app.post("/cameras")
def create_camera(camera: CameraCreate, admin=Depends(require_admin)):
    try:
        add_camera(
            camera_id=camera.camera_id,
            name=camera.name,
            password=camera.password
        )
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise HTTPException(status_code=409, detail="Camera ID already exists")
        raise HTTPException(status_code=500, detail="Failed to create camera")

    return {
        "message": "camera_created",
        "camera_id": camera.camera_id,
        "name": camera.name
    }


@app.patch("/cameras/{camera_id}/status")
def update_status(camera_id: str, payload: CameraStatusUpdate, admin=Depends(require_admin)):
    updated = update_camera_status(
        camera_id=camera_id,
        status=payload.status,
        ip_address=payload.ip_address
    )

    if not updated:
        raise HTTPException(status_code=404, detail="Camera not found")

    return {
        "message": "camera_status_updated",
        "camera_id": camera_id,
        "status": payload.status
    }


# ---------------------------------------------------------------------------
# Live streaming (WebView-compatible: header OR ?access_token=)
# ---------------------------------------------------------------------------

@app.get("/live/{camera_id}")
def live_stream(camera_id: str, current_user=Depends(get_current_user)):
    camera = get_camera(camera_id)

    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    if camera.get("status") != "online":
        raise HTTPException(status_code=409, detail="Camera is not online")

    ensure_camera_stream(camera_id)

    return StreamingResponse(
        mjpeg_frame_generator(camera_id),
        media_type=MJPEG_MEDIA_TYPE
    )


@app.get("/streams/{camera_id}/status")
def stream_status(camera_id: str, current_user=Depends(get_current_user)):
    return get_stream_status(camera_id)


# ---------------------------------------------------------------------------
# Recordings
# ---------------------------------------------------------------------------

@app.get("/recordings/status")
def all_recordings_status(current_user=Depends(get_current_user)):
    return get_auto_recording_status()


@app.post("/recordings/cleanup")
def cleanup_recordings(admin=Depends(require_admin)):
    try:
        deleted = cleanup_old_recordings()
    except RecordingStorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "message": "cleanup_done",
        "deleted": deleted,
        "deleted_count": len(deleted),
    }


@app.post("/recordings/{camera_id}/start")
def start_camera_recording(camera_id: str, admin=Depends(require_admin)):
    try:
        result = start_recording(camera_id)
    except RecordingStorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if result["message"] == "camera_not_found":
        raise HTTPException(status_code=404, detail="Camera not found")

    if result["message"] in ["camera_not_online", "camera_ip_unknown"]:
        raise HTTPException(status_code=409, detail=result["message"])

    return result


@app.post("/recordings/{camera_id}/stop")
def stop_camera_recording(camera_id: str, admin=Depends(require_admin)):
    return stop_recording(camera_id)


@app.get("/recordings/{camera_id}/status")
def camera_recording_status(camera_id: str, current_user=Depends(get_current_user)):
    return get_recording_status(camera_id)


@app.get("/recordings/{camera_id}")
def camera_recordings(camera_id: str, current_user=Depends(get_current_user)):
    return {
        "camera_id": camera_id,
        "recordings": list_recording_files(camera_id)
    }


@app.get("/recordings/{camera_id}/latest")
def latest_recording(camera_id: str, current_user=Depends(get_current_user)):
    return get_latest_recording(camera_id)


@app.get("/recordings/{camera_id}/file/{relative_path:path}")
def download_recording(camera_id: str, relative_path: str, current_user=Depends(get_current_user)):
    return recording_file_response(camera_id, relative_path)


# ---------------------------------------------------------------------------
# Playback page (WebView-compatible: header OR ?access_token=)
# ---------------------------------------------------------------------------

@app.get("/playback/{camera_id}")
def camera_playback_page(camera_id: str, access_token: str | None = None, current_user=Depends(get_current_user)):
    return playback_page(camera_id, access_token=access_token)


# ---------------------------------------------------------------------------
# Motion detection
# ---------------------------------------------------------------------------

@app.get("/motion/status")
def motion_status(current_user=Depends(get_current_user)):
    return get_motion_status()


@app.get("/motion/events")
def motion_events(current_user=Depends(get_current_user)):
    return {
        "events": get_motion_events()
    }


@app.get("/motion/events/{camera_id}")
def camera_motion_events(camera_id: str, current_user=Depends(get_current_user)):
    return {
        "camera_id": camera_id,
        "events": get_motion_events(camera_id)
    }

@app.get("/cameras/{camera_id}/heartbeat-history")
def camera_heartbeat_history(camera_id: str, hours: int = 24, current_user=Depends(get_current_user)):
    return {
        "camera_id": camera_id,
        "hours": hours,
        "heartbeats": list_heartbeat_history(camera_id, hours=hours)
    }


@app.get("/cameras/{camera_id}/reachability-history")
def camera_reachability_history(camera_id: str, hours: int = 24, current_user=Depends(get_current_user)):
    return {
        "camera_id": camera_id,
        "hours": hours,
        "probes": list_reachability_probes(camera_id, hours=hours)
    }


@app.get("/cameras/{camera_id}/rssi")
def get_rssi_history(camera_id: str, limit: int = 20, current_user=Depends(get_current_user)):
    return {
        "camera_id": camera_id,
        "readings": list_rssi_history(camera_id, limit=limit)
    }


# ---------------------------------------------------------------------------
# Camera telemetry: temperature, WiFi diagnostics, reboots & dropouts
# ---------------------------------------------------------------------------

@app.get("/cameras/{camera_id}/telemetry")
def camera_telemetry(camera_id: str, limit: int = 20, current_user=Depends(get_current_user)):
    limit = max(1, min(limit, 500))
    return {
        "camera_id": camera_id,
        "readings": list_telemetry(camera_id, limit=limit)
    }


@app.get("/cameras/{camera_id}/telemetry/latest")
def camera_telemetry_latest(camera_id: str, current_user=Depends(get_current_user)):
    return {
        "camera_id": camera_id,
        "latest": latest_telemetry(camera_id)
    }


@app.get("/cameras/{camera_id}/events")
def camera_events(camera_id: str, hours: int = 168, current_user=Depends(get_current_user)):
    hours = max(1, min(hours, 24 * 14))
    return {
        "camera_id": camera_id,
        "hours": hours,
        "events": list_events(camera_id, hours=hours)
    }


@app.get("/telemetry/overview")
def telemetry_overview(current_user=Depends(get_current_user)):
    return {"cameras": latest_for_all_cameras()}


# ---------------------------------------------------------------------------
# Firmware updates (OTA) and remote camera control
# ---------------------------------------------------------------------------

@app.post("/firmware")
async def upload_firmware(request: Request, version: str, latest: bool = True, camera: str | None = None, owner=Depends(require_control_user)):
    """Upload a compiled .bin as the raw request body, e.g.
    curl -X POST "http://server:8000/firmware?version=0.10.1" -H "Authorization: Bearer <token>" --data-binary @sketch.ino.bin
    Add &latest=false for a special build that should not become the default offered to every camera.
    Add &camera=cam012 for a build with that camera's identity compiled in: it can then only be sent to cam012.
    """
    data = await request.body()
    try:
        entry = save_firmware(version, data, make_latest=latest, camera_id=camera)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "firmware_saved", "firmware": entry}


@app.get("/firmware")
def firmware_list(camera_id: str | None = None, owner=Depends(require_control_user)):
    return list_firmware(camera_id)


@app.delete("/firmware/{version}")
def firmware_delete(version: str, owner=Depends(require_control_user)):
    if not delete_firmware(version):
        raise HTTPException(status_code=404, detail="No such firmware uploaded")
    return {"message": "firmware_deleted", "version": version}


@app.get("/firmware/download/{filename}")
def firmware_download(filename: str, t: str = ""):
    # No user login here on purpose: cameras download with a signed token that only
    # the backend can create (the .bin contains camera settings, so it is not public).
    if not verify_download_token(filename, t):
        raise HTTPException(status_code=403, detail="Invalid download token")
    path = firmware_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Firmware not found")
    return FileResponse(str(path), media_type="application/octet-stream", filename=path.name)


def _send_camera_command(camera_id: str, cmd: str, entry=None, require_online: bool = True):
    camera = get_camera(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    if require_online and camera.get("status") != "online":
        raise HTTPException(status_code=409, detail="Camera is not online")

    if mqtt_client is None or get_mqtt_status()["status"] != "connected":
        raise HTTPException(status_code=503, detail="MQTT broker not connected")

    try:
        payload = build_command(cmd, entry)
    except OtaConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # retain=False: a retained command would be re-run by the camera on every reconnect.
    mqtt_client.publish(f"survcam/cameras/{camera_id}/cmd", payload, qos=1, retain=False)


@app.post("/cameras/{camera_id}/ota")
def camera_ota(camera_id: str, body: OtaRequest | None = None, owner=Depends(require_control_user)):
    entry = get_firmware(body.version if body else None)
    if entry is None:
        raise HTTPException(status_code=404, detail="No such firmware uploaded")
    if entry.get("camera_id") and entry["camera_id"] != camera_id:
        raise HTTPException(
            status_code=409,
            detail=f"This firmware was built for {entry['camera_id']} and cannot be sent to {camera_id}",
        )
    _send_camera_command(camera_id, "ota", entry)
    return {"message": "ota_command_sent", "camera_id": camera_id, "version": entry["version"]}


@app.post("/cameras/{camera_id}/reboot")
def camera_reboot(camera_id: str, owner=Depends(require_control_user)):
    _send_camera_command(camera_id, "reboot")
    return {"message": "reboot_command_sent", "camera_id": camera_id}


@app.post("/cameras/{camera_id}/rollback")
def camera_rollback(camera_id: str, owner=Depends(require_control_user)):
    _send_camera_command(camera_id, "rollback")
    return {"message": "rollback_command_sent", "camera_id": camera_id}


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@app.post("/notifications/register-token")
def register_device_token(payload: DeviceTokenRequest, current_user=Depends(get_current_user)):
    register_token(payload.token)
    return {"message": "token_registered"}


@app.post("/notifications/unregister-token")
def unregister_device_token(payload: DeviceTokenRequest, current_user=Depends(get_current_user)):
    unregister_token(payload.token)
    return {"message": "token_unregistered"}


@app.post("/notifications/mute")
def mute_notifications(request: DeviceTokenRequest, hours: int = 1, current_user=Depends(get_current_user)):
    mute_device_for_hours(request.token, hours)
    return get_device_mute_status(request.token)


@app.post("/notifications/unmute")
def unmute_notifications(payload: DeviceTokenRequest, current_user=Depends(get_current_user)):
    unmute_device(payload.token)
    return {"muted": False}


@app.post("/notifications/mute-status")
def notification_mute_status(payload: DeviceTokenRequest, current_user=Depends(get_current_user)):
    return get_device_mute_status(payload.token)


@app.get("/notifications/history")
def notification_history(limit: int = 100, current_user=Depends(get_current_user)):
    return {"notifications": get_history(limit=limit)}


@app.get("/system/health")
def system_health(current_user=Depends(get_current_user)):
    cameras = list_cameras()
    online_count = sum(1 for c in cameras if c.get("status") == "online")

    # Muting is per device now (see /notifications/mute-status, which takes the
    # device token). The old global get_mute_status() no longer exists, so these
    # two fields are kept only so older app builds that still read them keep working.
    return {
        "cameras_online": online_count,
        "cameras_total": len(cameras),
        "muted": False,
        "muted_until": None,
    }
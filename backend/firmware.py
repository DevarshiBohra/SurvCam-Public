"""
firmware.py - stores firmware images and builds the SIGNED commands the cameras accept
(update / reboot / roll back).

Why signed: your MQTT broker accepts anyone on the network (the cameras connect without
a username or password), so a camera must not obey a command unless it carries a valid
HMAC signature made with the shared OTA key. The key never travels over the network.

Setup (once):
  1. Make a long random key:      openssl rand -hex 24
  2. Put it in the camera sketch: const char* OTA_KEY = "<the key>";
  3. Put the SAME key on the server, either
       - in a file named ota_key.txt next to this file, or
       - in the environment variable SURVCAM_OTA_KEY
  4. Tell the cameras where to download from (must be reachable FROM THE CAMERAS):
       environment variable SURVCAM_FIRMWARE_BASE_URL, default http://127.0.0.1:8000 (configure this for your LAN)
"""

import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
FIRMWARE_DIR = BASE_DIR / "firmware"
MANIFEST_PATH = FIRMWARE_DIR / "manifest.json"
KEY_FILE = BASE_DIR / "ota_key.txt"

FIRMWARE_BASE_URL = os.environ.get("SURVCAM_FIRMWARE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

MIN_IMAGE_BYTES = 100_000
MAX_IMAGE_BYTES = 1_900_000          # must fit the camera's OTA slot (1.9 MB scheme)
VERSION_RE = re.compile(r"^[0-9A-Za-z._-]{1,24}$")
CAMERA_RE = re.compile(r"^[0-9A-Za-z_-]{3,24}$")


class OtaConfigError(Exception):
    pass


def _key() -> bytes:
    key = os.environ.get("SURVCAM_OTA_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
    if len(key) < 16 or key.startswith("REPLACE_") or key == "CHANGE-ME-to-a-long-random-key":
        raise OtaConfigError(
            "OTA key not configured (need 16+ characters in ota_key.txt or SURVCAM_OTA_KEY)"
        )
    return key.encode("utf-8")


def _hmac_hex(message: str) -> str:
    return hmac.new(_key(), message.encode("utf-8"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Stored images
# ---------------------------------------------------------------------------

def _load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        try:
            return json.loads(MANIFEST_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {"latest": None, "versions": []}


def _save_manifest(manifest: dict):
    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))


def save_firmware(version: str, data: bytes, make_latest: bool = True, camera_id=None) -> dict:
    """Validate and store a firmware image. make_latest=False keeps the current default
    (use it for special builds that only some cameras should get).
    camera_id tags a build made for ONE specific camera (identity compiled in): the backend
    then refuses to send it to any other camera."""
    if not VERSION_RE.match(version):
        raise ValueError("Version may only contain letters, digits, '.', '_' and '-' (max 24)")
    if not (MIN_IMAGE_BYTES <= len(data) <= MAX_IMAGE_BYTES):
        raise ValueError(f"Image size {len(data)} bytes is outside the allowed range")
    if data[0] != 0xE9:
        raise ValueError("Not an ESP32 app image (first byte is not 0xE9) - did you upload the .bin?")

    if camera_id is not None and not CAMERA_RE.match(camera_id):
        raise ValueError("camera must be 3-24 letters, digits, - or _")

    FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"survcam-{version}.bin"
    (FIRMWARE_DIR / filename).write_bytes(data)

    entry = {
        "version": version,
        "filename": filename,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "camera_id": camera_id,   # None = generic build, fine for every camera
    }

    manifest = _load_manifest()
    manifest["versions"] = [v for v in manifest["versions"] if v["version"] != version]
    manifest["versions"].append(entry)
    if make_latest or not manifest.get("latest"):
        manifest["latest"] = version
    _save_manifest(manifest)
    return entry


def list_firmware(camera_id=None) -> dict:
    """With camera_id: only builds that camera may receive (generic ones + ones tagged for it)."""
    manifest = _load_manifest()
    versions = manifest["versions"]
    if camera_id:
        versions = [v for v in versions if v.get("camera_id") in (None, camera_id)]
    versions = sorted(versions, key=lambda v: v["uploaded_at"], reverse=True)
    latest = manifest["latest"]
    if latest not in [v["version"] for v in versions]:
        latest = None
    return {"latest": latest, "versions": versions}


def get_firmware(version=None):
    manifest = _load_manifest()
    wanted = version or manifest["latest"]
    for entry in manifest["versions"]:
        if entry["version"] == wanted:
            return entry
    return None


def delete_firmware(version: str) -> bool:
    manifest = _load_manifest()
    entry = next((v for v in manifest["versions"] if v["version"] == version), None)
    if entry is None:
        return False
    path = FIRMWARE_DIR / entry["filename"]
    if path.is_file():
        path.unlink()
    manifest["versions"] = [v for v in manifest["versions"] if v["version"] != version]
    if manifest["latest"] == version:
        remaining = sorted(manifest["versions"], key=lambda v: v["uploaded_at"], reverse=True)
        manifest["latest"] = remaining[0]["version"] if remaining else None
    _save_manifest(manifest)
    return True


# ---------------------------------------------------------------------------
# Download tokens (cameras download without a user login)
# ---------------------------------------------------------------------------

def download_token(filename: str) -> str:
    return _hmac_hex("dl|" + filename)


def verify_download_token(filename: str, token: str) -> bool:
    try:
        return hmac.compare_digest(download_token(filename), token or "")
    except OtaConfigError:
        return False


def firmware_path(filename: str):
    """Safe path inside the firmware folder, or None."""
    safe = Path(filename).name
    path = FIRMWARE_DIR / safe
    return path if path.is_file() and safe.endswith(".bin") else None


# ---------------------------------------------------------------------------
# Signed commands
# ---------------------------------------------------------------------------

def build_command(cmd: str, entry=None) -> str:
    """
    Returns the JSON text to publish on survcam/cameras/<id>/cmd.
    cmd: "ota" (needs entry), "reboot" or "rollback".
    seq is the current epoch second: strictly increasing, so a captured command
    cannot be replayed later (cameras remember the last seq they accepted).
    """
    seq = int(time.time())
    version = sha = url = ""
    payload = {"cmd": cmd, "seq": seq}

    if cmd == "ota":
        if entry is None:
            raise ValueError("ota command needs a firmware entry")
        version = entry["version"]
        sha = entry["sha256"]
        url = (
            f"{FIRMWARE_BASE_URL}/firmware/download/{entry['filename']}"
            f"?t={download_token(entry['filename'])}"
        )
        payload.update({"version": version, "sha256": sha, "size": entry["size"], "url": url})

    # Must match the string the camera rebuilds: cmd|seq|version|sha256|url
    payload["sig"] = _hmac_hex(f"{cmd}|{seq}|{version}|{sha}|{url}")
    return json.dumps(payload, separators=(",", ":"))
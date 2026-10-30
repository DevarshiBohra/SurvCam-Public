"""Configurable recording storage paths for SurvCam."""
import os
import re
from pathlib import Path

DATA_DIR = Path(os.environ.get("SURVCAM_DATA_DIR", Path(__file__).resolve().parent / "data"))
RECORDINGS_ROOT = Path(os.environ.get("SURVCAM_RECORDINGS_ROOT", DATA_DIR / "recordings"))
SNAPSHOT_ROOT = Path(os.environ.get("SURVCAM_SNAPSHOT_ROOT", DATA_DIR / "thumbnails"))
WD_MOUNT = Path(os.environ.get("SURVCAM_STORAGE_MOUNT", Path(RECORDINGS_ROOT).parent))
WD_DEVICE_UUID = os.environ.get("SURVCAM_STORAGE_UUID", "").strip()
WD_DEVICE_LINK = Path("/dev/disk/by-uuid") / WD_DEVICE_UUID if WD_DEVICE_UUID else None
_CAMERA_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class RecordingStorageUnavailable(RuntimeError):
    pass


def _mounted_source(mountpoint: Path) -> Path | None:
    def unescape_mount_field(value: str) -> str:
        return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)

    try:
        lines = Path("/proc/self/mounts").read_text().splitlines()
    except OSError:
        return None
    source = None
    for line in lines:
        fields = line.split()
        if len(fields) >= 2 and unescape_mount_field(fields[1]) == str(mountpoint):
            source = Path(unescape_mount_field(fields[0]))
    return source


def ensure_recording_storage():
    """Create local paths, and validate a configured UUID-backed mount if used."""
    if WD_DEVICE_LINK is not None:
        if not WD_DEVICE_LINK.exists():
            raise RecordingStorageUnavailable(f"Configured recordings device UUID {WD_DEVICE_UUID} is not present")
        try:
            os.statvfs(WD_MOUNT)
            expected = WD_DEVICE_LINK.resolve(strict=True)
            mounted = _mounted_source(WD_MOUNT)
            if not os.path.ismount(WD_MOUNT) or mounted is None or mounted.resolve(strict=True) != expected:
                raise RecordingStorageUnavailable(f"Expected recordings disk is not mounted at {WD_MOUNT}")
        except RecordingStorageUnavailable:
            raise
        except OSError as exc:
            raise RecordingStorageUnavailable(f"Recordings disk is unavailable at {WD_MOUNT}: {exc}") from exc
    try:
        RECORDINGS_ROOT.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RecordingStorageUnavailable(f"Recording storage is not writable: {exc}") from exc


def storage_is_available() -> bool:
    try:
        ensure_recording_storage()
    except RecordingStorageUnavailable:
        return False
    return True


def camera_recordings_dir(camera_id: str) -> Path:
    if not _CAMERA_ID_PATTERN.fullmatch(camera_id):
        raise ValueError(f"Invalid camera ID: {camera_id}")
    ensure_recording_storage()
    return RECORDINGS_ROOT / camera_id

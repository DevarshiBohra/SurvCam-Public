"""
auto_recorder.py

Handles disk space management only.
Recording start/stop decisions have moved to recording_scheduler.py.

Responsibilities:
  - Age-based cleanup (delete folders older than RETENTION_DAYS)
  - Emergency cleanup (delete oldest day when disk is critically low)
  - Stop all active recordings if disk cannot be freed
  - Expose disk/retention status via get_auto_recording_status()
"""

import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from database import list_cameras
from recorder import stop_recording, get_recording_status
from recording_storage import (
    WD_MOUNT,
    RECORDINGS_ROOT,
    SNAPSHOT_ROOT,
    ensure_recording_storage,
    storage_is_available,
)

RETENTION_DAYS  = 9
MIN_FREE_BYTES  = 30 * 1024 * 1024 * 1024   # 30 GB safety floor
CHECK_INTERVAL_SECONDS = 60                   # disk check every 60s is plenty

_auto_thread = None
_stop_event  = threading.Event()


# ─────────────────────────────────────────────────────────────────────────────
# Disk helpers
# ─────────────────────────────────────────────────────────────────────────────

def _free_space_bytes(path: str | Path = WD_MOUNT) -> int:
    ensure_recording_storage()
    return shutil.disk_usage(path).free


def _delete_matching_snapshots(recording_day_dir: Path):
    snapshot_day_dir = (
        SNAPSHOT_ROOT / recording_day_dir.parent.name / recording_day_dir.name
    )
    if snapshot_day_dir.is_dir():
        shutil.rmtree(snapshot_day_dir)
        print(f"Snapshot cleanup deleted: {snapshot_day_dir}")


# ─────────────────────────────────────────────────────────────────────────────
# Cleanup functions (also called from main.py's /recordings/cleanup endpoint)
# ─────────────────────────────────────────────────────────────────────────────

def cleanup_old_recordings():
    """Delete expired recording folders and their matching snapshots."""
    ensure_recording_storage()
    cutoff = datetime.now().date() - timedelta(days=RETENTION_DAYS)

    if not RECORDINGS_ROOT.exists():
        return []

    deleted = []

    for camera_dir in RECORDINGS_ROOT.iterdir():
        if not camera_dir.is_dir():
            continue

        for day_dir in camera_dir.iterdir():
            if not day_dir.is_dir():
                continue

            try:
                folder_date = datetime.strptime(day_dir.name, "%Y-%m-%d").date()
            except ValueError:
                continue

            if folder_date < cutoff:
                shutil.rmtree(day_dir)
                _delete_matching_snapshots(day_dir)
                deleted.append(str(day_dir))
                print(f"Retention cleanup deleted: {day_dir}")

    return deleted


def cleanup_oldest_recording_day():
    """Delete the oldest non-current recording date and its snapshots."""
    ensure_recording_storage()

    if not RECORDINGS_ROOT.exists():
        return None

    date_to_dirs: dict = {}

    for camera_dir in RECORDINGS_ROOT.iterdir():
        if not camera_dir.is_dir():
            continue

        for day_dir in camera_dir.iterdir():
            if not day_dir.is_dir():
                continue

            try:
                folder_date = datetime.strptime(day_dir.name, "%Y-%m-%d").date()
            except ValueError:
                continue

            if folder_date >= datetime.now().date():
                continue

            date_to_dirs.setdefault(folder_date, []).append(day_dir)

    if not date_to_dirs:
        return None

    oldest_date = min(date_to_dirs.keys())
    deleted = []

    for day_dir in date_to_dirs[oldest_date]:
        shutil.rmtree(day_dir)
        _delete_matching_snapshots(day_dir)
        deleted.append(str(day_dir))
        print(f"Emergency cleanup deleted: {day_dir}")

    return (
        f"WD: deleted {oldest_date} folders "
        f"({len(deleted)} cameras)"
    )


def _stop_all_recordings(reason: str):
    print(f"[DISK] {reason}; stopping all camera recordings")
    for camera in list_cameras():
        camera_id = camera["camera_id"]
        try:
            if get_recording_status(camera_id).get("recording", False):
                stop_recording(camera_id)
                print(f"[DISK] Stopped recording: {camera_id}")
        except Exception as exc:
            print(f"[DISK] Failed to stop {camera_id}: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Background loop
# ─────────────────────────────────────────────────────────────────────────────

def _disk_management_loop():
    print("Disk manager started")

    while not _stop_event.is_set():
        try:
            if not storage_is_available():
                _stop_all_recordings("WD recordings disk unavailable")
                _stop_event.wait(CHECK_INTERVAL_SECONDS)
                continue

            cleanup_old_recordings()
            free_bytes = _free_space_bytes()

            if free_bytes < MIN_FREE_BYTES:
                print(
                    f"[DISK] WD disk low: "
                    f"{free_bytes / 1024 ** 3:.2f} GiB free — "
                    f"starting emergency cleanup"
                )

                while free_bytes < MIN_FREE_BYTES:
                    result = cleanup_oldest_recording_day()
                    if result is None:
                        print("[DISK] WD: no older recording folders left")
                        break
                    print(f"[DISK] Emergency: {result}")
                    free_bytes = _free_space_bytes()

                if free_bytes < MIN_FREE_BYTES:
                    _stop_all_recordings("WD remains below the 30 GiB free-space floor")

        except Exception as exc:
            print(f"[DISK] Disk manager loop error: {exc}")

        _stop_event.wait(CHECK_INTERVAL_SECONDS)

    print("Disk manager stopped")


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def start_auto_recorder():
    """Name kept for backward compatibility with main.py."""
    global _auto_thread

    if _auto_thread and _auto_thread.is_alive():
        return

    _stop_event.clear()
    _auto_thread = threading.Thread(
        target=_disk_management_loop,
        daemon=True,
        name="disk-manager",
    )
    _auto_thread.start()


def stop_auto_recorder():
    """Name kept for backward compatibility with main.py."""
    _stop_event.set()


def get_auto_recording_status():
    """Called by GET /recordings/status in main.py."""
    cameras = list_cameras()
    result = []

    for camera in cameras:
        camera_id = camera["camera_id"]
        rec_status = get_recording_status(camera_id)

        result.append({
            "camera_id":     camera_id,
            "name":          camera.get("name"),
            "camera_status": camera.get("status"),
            "ip_address":    camera.get("ip_address"),
            "recording":     rec_status.get("recording", False),
            "encoder":       rec_status.get("encoder"),
        })

    storage_available = storage_is_available()
    camera_ids = []
    free_bytes = None
    if storage_available:
        camera_ids = sorted(
            camera_dir.name
            for camera_dir in RECORDINGS_ROOT.iterdir()
            if camera_dir.is_dir()
        ) if RECORDINGS_ROOT.exists() else []
        free_bytes = _free_space_bytes()

    return {
        "disk_manager":  "running",
        "storage_available": storage_available,
        "retention_days": RETENTION_DAYS,
        "min_free_gb":   round(MIN_FREE_BYTES / 1024 ** 3, 2),
        "free_gb": round(free_bytes / 1024 ** 3, 2) if free_bytes is not None else None,
        "storage_volumes": [{
            "path": str(WD_MOUNT),
            "camera_ids": camera_ids,
            "free_gb": round(free_bytes / 1024 ** 3, 2) if free_bytes is not None else None,
            "min_free_gb": round(MIN_FREE_BYTES / 1024 ** 3, 2),
        }],
        "cameras":       result,
    }
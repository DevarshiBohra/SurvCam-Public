import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from recording_storage import RecordingStorageUnavailable, camera_recordings_dir

SUPPORTED_EXTENSIONS = {".mp4", ".mkv"}


def _camera_root(camera_id: str) -> Path:
    try:
        return camera_recordings_dir(camera_id)
    except RecordingStorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def list_recording_days(camera_id: str):
    """All dates (YYYY-MM-DD) that have at least one recording folder,
    newest first."""
    camera_root = _camera_root(camera_id)

    if not camera_root.exists():
        return []

    days = []

    for day_dir in camera_root.iterdir():
        if not day_dir.is_dir():
            continue

        try:
            datetime.strptime(day_dir.name, "%Y-%m-%d")
        except ValueError:
            continue

        days.append(day_dir.name)

    days.sort(reverse=True)
    return days


def list_recording_hours(camera_id: str, date: str):
    """For a given date, which of the 24 hours have at least one segment."""
    day_dir = _camera_root(camera_id) / date

    if not day_dir.exists():
        raise HTTPException(status_code=404, detail="No recordings for this date")

    hours_with_recordings = set()

    for file_path in day_dir.iterdir():
        if not file_path.is_file() or file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        # Filenames are written as "%H-%M-%S.mp4" by recorder.py
        try:
            hour = int(file_path.name.split("-")[0])
        except (ValueError, IndexError):
            continue

        hours_with_recordings.add(hour)

    return [
        {"hour": hour, "has_recording": hour in hours_with_recordings}
        for hour in range(24)
    ]


def _probe_duration_seconds(file_path: Path) -> float:
    """Real duration of a segment via ffprobe. Segments are usually ~300s,
    but the first/last segment of a recording run can be shorter, so this
    is never assumed - it's measured."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return round(float(result.stdout.strip()), 2)
    except Exception:
        # Fall back to the nominal segment length if ffprobe fails for any
        # reason (corrupt file, missing binary, etc.) rather than crashing
        # manifest generation entirely.
        return 300.0


def get_hour_manifest(camera_id: str, date: str, hour: int):
    """Ordered list of segments covering one clock hour, with real start
    times and durations, for the app to feed into an ExoPlayer playlist."""
    day_dir = _camera_root(camera_id) / date

    if not day_dir.exists():
        raise HTTPException(status_code=404, detail="No recordings for this date")

    if hour < 0 or hour > 23:
        raise HTTPException(status_code=400, detail="Hour must be between 0 and 23")

    hour_prefix = f"{hour:02d}-"
    segments = []

    try:
        base_date = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Date must be in YYYY-MM-DD format")

    for file_path in sorted(day_dir.iterdir()):
        if not file_path.is_file() or file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        if not file_path.name.startswith(hour_prefix):
            continue

        try:
            h, m, s = (int(part) for part in file_path.stem.split("-"))
            start_dt = base_date.replace(hour=h, minute=m, second=s)
        except ValueError:
            continue

        duration = _probe_duration_seconds(file_path)
        relative_path = file_path.relative_to(_camera_root(camera_id)).as_posix()

        segments.append({
            "filename": file_path.name,
            "relative_path": relative_path,
            "start_time": start_dt.isoformat(),
            "duration_seconds": duration,
            "download_url": f"/recordings/{camera_id}/file/{relative_path}",
        })

    segments.sort(key=lambda seg: seg["start_time"])

    if not segments:
        raise HTTPException(status_code=404, detail="No recordings for this hour")

    return {
        "camera_id": camera_id,
        "date": date,
        "hour": hour,
        "segments": segments,
    }
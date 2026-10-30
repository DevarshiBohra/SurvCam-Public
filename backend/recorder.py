import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from database import get_camera
from recording_storage import camera_recordings_dir
from stream import ensure_camera_stream
from auth import create_access_token


SEGMENT_SECONDS = 300
BACKEND_BASE_URL = "http://127.0.0.1:8000"

# VAAPI hardware encoding - confirmed working on this server's Sandy Bridge
# iGPU (Intel Quick Sync via the i965 driver), tested at 12 concurrent
# sessions with no errors. Bitrate-based (not CRF) since the older i965
# driver doesn't reliably support quality-based rate control.
USE_VAAPI = True
VAAPI_DEVICE = "/dev/dri/renderD128"
VAAPI_BITRATE = "1400k" # was 1100k 
VAAPI_MAXRATE = "1900k" # was 1500k
VAAPI_BUFSIZE = "2700k" # was 2100k
# How long to wait after launching a VAAPI ffmpeg process before checking
# whether it's actually still alive. Long enough for a real init failure
# (bad device, driver hiccup) to have already caused ffmpeg to exit, short
# enough not to meaningfully delay recording start.
VAAPI_STARTUP_CHECK_SECONDS = 2.0

recording_processes = {}
recording_modes = {}

# recorder.py runs inside the trusted backend process itself, so instead of
# logging in over HTTP like a real client would, it mints its own internal
# token for the admin account at import time. This lets ffmpeg's plain HTTP
# request to /live/{camera_id} pass the get_current_user auth check via the
# ?access_token= query parameter, without needing a password or login flow.
_INTERNAL_SERVICE_TOKEN = create_access_token("admin", "admin")


def camera_root_dir(camera_id: str) -> Path:
    return camera_recordings_dir(camera_id)


def ensure_date_dirs(camera_id: str):
    """Pre-creates today's and tomorrow's recording folders for a camera.

    ffmpeg's segment muxer expands %Y-%m-%d in the output path live, per
    segment, via -strftime 1 - but it does NOT create directories itself.
    Without this, a long-running recording process would silently fail to
    write the first segment of a new day the moment midnight passes,
    because the new day's folder wouldn't exist yet. Called frequently
    from auto_recorder.py's loop so the next day's folder always exists
    well before it's actually needed.
    """
    root = camera_root_dir(camera_id)
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    (root / today).mkdir(parents=True, exist_ok=True)
    (root / tomorrow).mkdir(parents=True, exist_ok=True)


def is_recording(camera_id: str) -> bool:
    process = recording_processes.get(camera_id)

    if process is None:
        return False

    return process.poll() is None


def _base_ffmpeg_args(stream_url: str):
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel", "warning",
        "-y",
        "-use_wallclock_as_timestamps", "1",
        "-rw_timeout", "15000000",
        "-i", stream_url,
        "-an",
    ]


def _base_segment_args(output_pattern: str):
    return [
        "-f", "segment",
        "-segment_time", str(SEGMENT_SECONDS),
        "-segment_atclocktime", "1",
        "-segment_format", "mp4",
        "-segment_format_options", "movflags=+faststart",
        "-reset_timestamps", "1",
        "-strftime", "1",
        output_pattern,
    ]


def _build_vaapi_command(stream_url: str, output_pattern: str):
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning", "-y"]
    command += ["-vaapi_device", VAAPI_DEVICE]
    command += [
        "-use_wallclock_as_timestamps", "1",
        "-rw_timeout", "15000000",
        "-i", stream_url,
        "-an",
        "-vf", "format=nv12,hwupload",
        "-c:v", "h264_vaapi",
        "-b:v", VAAPI_BITRATE,
        "-maxrate", VAAPI_MAXRATE,
        "-bufsize", VAAPI_BUFSIZE,
    ]
    command += _base_segment_args(output_pattern)
    return command


def _build_libx264_command(stream_url: str, output_pattern: str):
    command = _base_ffmpeg_args(stream_url)
    command += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-vsync", "vfr",
    ]
    command += _base_segment_args(output_pattern)
    return command


def _launch(command):
    return subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def start_recording(camera_id: str):
    if is_recording(camera_id):
        return {
            "recording": True,
            "message": "already_recording"
        }

    camera = get_camera(camera_id)

    if camera is None:
        return {
            "recording": False,
            "message": "camera_not_found"
        }

    if camera.get("status") != "online":
        return {
            "recording": False,
            "message": "camera_not_online"
        }

    ensure_camera_stream(camera_id)
    ensure_date_dirs(camera_id)

    output_pattern = str(camera_root_dir(camera_id) / "%Y-%m-%d" / "%H-%M-%S.mp4")
    stream_url = f"{BACKEND_BASE_URL}/live/{camera_id}?access_token={_INTERNAL_SERVICE_TOKEN}"

    mode = "libx264"
    process = None
    fallback_reason = None

    if USE_VAAPI:
        vaapi_command = _build_vaapi_command(stream_url, output_pattern)
        vaapi_process = _launch(vaapi_command)

        # Give it a moment - a real VAAPI init failure (bad device, driver
        # busy, etc.) causes ffmpeg to exit almost immediately. If it's
        # still alive after this, treat it as a genuine success.
        time.sleep(VAAPI_STARTUP_CHECK_SECONDS)

        if vaapi_process.poll() is None:
            process = vaapi_process
            mode = "vaapi"
        else:
            stderr_output = ""
            try:
                stderr_output = vaapi_process.stderr.read()
            except Exception:
                pass
            fallback_reason = stderr_output.strip()[-500:] if stderr_output else "unknown error"
            print(f"VAAPI recording failed to start for {camera_id}, falling back to libx264: {fallback_reason}")

    if process is None:
        libx264_command = _build_libx264_command(stream_url, output_pattern)
        process = _launch(libx264_command)
        mode = "libx264"

    recording_processes[camera_id] = process
    recording_modes[camera_id] = mode

    return {
        "recording": True,
        "message": "recording_started",
        "camera_id": camera_id,
        "encoder": mode,
        "vaapi_fallback_reason": fallback_reason,
        "stream_url": f"{BACKEND_BASE_URL}/live/{camera_id}",
        "output_pattern": output_pattern,
        "segment_seconds": SEGMENT_SECONDS,
        "format": "mp4",
        "video_codec": "h264"
    }


def stop_recording(camera_id: str):
    process = recording_processes.get(camera_id)

    if process is None or process.poll() is not None:
        recording_processes.pop(camera_id, None)
        recording_modes.pop(camera_id, None)
        return {
            "recording": False,
            "message": "not_recording"
        }

    process.terminate()

    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)

    recording_processes.pop(camera_id, None)
    recording_modes.pop(camera_id, None)

    return {
        "recording": False,
        "message": "recording_stopped",
        "camera_id": camera_id
    }


def get_recording_status(camera_id: str):
    return {
        "camera_id": camera_id,
        "recording": is_recording(camera_id),
        "encoder": recording_modes.get(camera_id),
    }
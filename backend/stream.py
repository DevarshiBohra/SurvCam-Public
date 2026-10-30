import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from database import get_camera


MJPEG_BOUNDARY = "survframe123"
MJPEG_MEDIA_TYPE = f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}"


@dataclass
class CameraStreamState:
    camera_id: str
    thread: Optional[threading.Thread] = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    condition: threading.Condition = field(default_factory=threading.Condition)
    latest_frame: Optional[bytes] = None
    frame_seq: int = 0
    connected: bool = False
    last_error: Optional[str] = None
    last_frame_time: Optional[float] = None
    source_url: Optional[str] = None


_streams = {}
_streams_lock = threading.Lock()


def _camera_source_url(camera_id: str):
    camera = get_camera(camera_id)

    if camera is None:
        return None, "camera_not_found"

    if camera.get("status") != "online":
        return None, "camera_not_online"

    ip_address = camera.get("ip_address")

    if not ip_address:
        return None, "camera_ip_unknown"

    return f"http://{ip_address}:81/stream", None


def _read_mjpeg_stream(state: CameraStreamState):
    while not state.stop_event.is_set():
        source_url, error = _camera_source_url(state.camera_id)

        if error:
            state.connected = False
            state.last_error = error
            time.sleep(2)
            continue

        state.source_url = source_url

        try:
            request = Request(
                source_url,
                headers={"User-Agent": "SurvCam-StreamManager/0.1"}
            )

            with urlopen(request, timeout=10) as response:
                state.connected = True
                state.last_error = None

                while not state.stop_event.is_set():
                    line = response.readline()

                    if not line:
                        raise RuntimeError("camera stream ended")

                    if b"--" not in line:
                        continue

                    content_length = None

                    while True:
                        header_line = response.readline()

                        if not header_line or header_line in (b"\r\n", b"\n"):
                            break

                        header_text = header_line.decode("utf-8", errors="ignore").strip()
                        lower = header_text.lower()

                        if lower.startswith("content-length:"):
                            content_length = int(header_text.split(":", 1)[1].strip())

                    if not content_length:
                        continue

                    frame = response.read(content_length)

                    if len(frame) != content_length:
                        raise RuntimeError("incomplete jpeg frame")

                    with state.condition:
                        state.latest_frame = frame
                        state.frame_seq += 1
                        state.last_frame_time = time.time()
                        state.condition.notify_all()

        except (HTTPError, URLError, TimeoutError, RuntimeError, OSError) as exc:
            state.connected = False
            state.last_error = str(exc)
            time.sleep(2)


def ensure_camera_stream(camera_id: str):
    with _streams_lock:
        state = _streams.get(camera_id)

        if state is None:
            state = CameraStreamState(camera_id=camera_id)
            _streams[camera_id] = state

        if state.thread is None or not state.thread.is_alive():
            state.stop_event.clear()
            state.thread = threading.Thread(
                target=_read_mjpeg_stream,
                args=(state,),
                daemon=True
            )
            state.thread.start()

        return state


def get_stream_status(camera_id: str):
    state = ensure_camera_stream(camera_id)

    return {
        "camera_id": camera_id,
        "running": state.thread is not None and state.thread.is_alive(),
        "connected": state.connected,
        "source_url": state.source_url,
        "frame_seq": state.frame_seq,
        "last_frame_time": state.last_frame_time,
        "last_error": state.last_error,
    }


async def mjpeg_frame_generator(camera_id: str):
    state = ensure_camera_stream(camera_id)
    last_seq = -1
    idle_timeout = 30
    last_new_frame = time.monotonic()

    while True:
        with state.condition:
            frame = state.latest_frame
            seq = state.frame_seq

        if frame is not None and seq != last_seq:
            last_seq = seq
            last_new_frame = time.monotonic()

            yield (
                f"--{MJPEG_BOUNDARY}\r\n"
                "Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(frame)}\r\n"
                "\r\n"
            ).encode("utf-8")

            yield frame
            yield b"\r\n"
        else:
            if time.monotonic() - last_new_frame > idle_timeout:
                return
            await asyncio.sleep(0.02)


def get_latest_frame_bytes(camera_id: str):
    state = _streams.get(camera_id)

    if state is None:
        return None

    return state.latest_frame

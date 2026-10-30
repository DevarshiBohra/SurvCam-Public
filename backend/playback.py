from pathlib import Path
from datetime import datetime
from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from recording_storage import RecordingStorageUnavailable, camera_recordings_dir


SUPPORTED_EXTENSIONS = {".mp4", ".mkv"}


def _camera_root(camera_id: str) -> Path:
  try:
    return camera_recordings_dir(camera_id)
  except RecordingStorageUnavailable as exc:
    raise HTTPException(status_code=503, detail=str(exc)) from exc


def _safe_recording_path(camera_id: str, relative_path: str) -> Path:
    camera_root = _camera_root(camera_id).resolve()
    file_path = (camera_root / relative_path).resolve()

    if not str(file_path).startswith(str(camera_root)):
        raise HTTPException(status_code=400, detail="Invalid recording path")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found")

    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported recording format")

    return file_path


def _media_type_for(file_path: Path) -> str:
    suffix = file_path.suffix.lower()

    if suffix == ".mp4":
        return "video/mp4"

    if suffix == ".mkv":
        return "video/x-matroska"

    return "application/octet-stream"


def list_recording_files(camera_id: str):
    camera_root = _camera_root(camera_id)

    if not camera_root.exists():
        return []

    recordings = []

    for file_path in camera_root.rglob("*"):
        if not file_path.is_file():
            continue

        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        stat = file_path.stat()
        relative_path = file_path.relative_to(camera_root).as_posix()

        recordings.append({
            "camera_id": camera_id,
            "filename": file_path.name,
            "relative_path": relative_path,
            "file_path": str(file_path),
            "format": file_path.suffix.lower().lstrip("."),
            "size_bytes": stat.st_size,
            "size_mb": round(stat.st_size / 1024 / 1024, 2),
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "download_url": f"/recordings/{camera_id}/file/{relative_path}",
        })

    recordings.sort(key=lambda item: item["modified_at"], reverse=True)
    return recordings


def get_latest_recording(camera_id: str):
    recordings = list_recording_files(camera_id)

    if not recordings:
        raise HTTPException(status_code=404, detail="No recordings found")

    return recordings[0]


def recording_file_response(camera_id: str, relative_path: str):
    file_path = _safe_recording_path(camera_id, relative_path)

    return FileResponse(
        path=str(file_path),
        media_type=_media_type_for(file_path),
        filename=file_path.name
    )


def playback_page(camera_id: str, access_token: str | None = None):
    recordings = list_recording_files(camera_id)

    rows = ""

    if not recordings:
        rows = """
        <tr>
          <td colspan="5" class="empty">No recordings found yet.</td>
        </tr>
        """
    else:
        for rec in recordings:
            # Append the access_token to each download link so the browser/
            # WebView can authenticate the file download too. The main GET
            # /recordings/{camera_id}/file/{relative_path} route is protected
            # and only accepts the token via header OR ?access_token=.
            download_url = rec["download_url"]

            if access_token:
                download_url = f"{download_url}?access_token={access_token}"

            rows += f"""
            <tr>
              <td>{rec["relative_path"]}</td>
              <td>{rec["format"].upper()}</td>
              <td>{rec["size_mb"]} MB</td>
              <td>{rec["modified_at"]}</td>
              <td>
                <a class="button" href="{download_url}">Download</a>
              </td>
            </tr>
            """

    html = f"""
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>SurvCam Playback - {camera_id}</title>
      <style>
        body {{
          font-family: Arial, sans-serif;
          background: #111827;
          color: #e5e7eb;
          margin: 0;
          padding: 24px;
        }}
        h1 {{
          margin: 0 0 8px;
          font-size: 24px;
        }}
        .sub {{
          color: #9ca3af;
          margin-bottom: 24px;
        }}
        table {{
          width: 100%;
          border-collapse: collapse;
          background: #1f2937;
        }}
        th, td {{
          text-align: left;
          padding: 12px;
          border-bottom: 1px solid #374151;
          font-size: 14px;
        }}
        th {{
          color: #93c5fd;
          background: #111827;
        }}
        .button {{
          display: inline-block;
          background: #2563eb;
          color: white;
          padding: 8px 12px;
          border-radius: 6px;
          text-decoration: none;
          font-size: 13px;
        }}
        .empty {{
          color: #9ca3af;
          text-align: center;
          padding: 32px;
        }}
        .note {{
          margin-top: 18px;
          color: #9ca3af;
          font-size: 13px;
        }}
      </style>
    </head>
    <body>
      <h1>Recordings: {camera_id}</h1>
      <div class="sub">Newest recordings are shown first.</div>

      <table>
        <thead>
          <tr>
            <th>File</th>
            <th>Format</th>
            <th>Size</th>
            <th>Modified</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>

      <div class="note">
        New recordings are saved as MP4 for better Android playback. Older MKV files may require VLC.
      </div>
    </body>
    </html>
    """

    return HTMLResponse(html)
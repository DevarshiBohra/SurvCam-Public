"""
Deletes motion-snapshot day folders after SNAPSHOT_RETENTION_DAYS.

Snapshots live under SNAPSHOT_ROOT/<camera_id>/<YYYY-MM-DD>/ and are cleaned here on
their own schedule, independent of the recordings (which are deleted after a few days).
"""

import shutil
from datetime import datetime, timedelta

from recording_storage import SNAPSHOT_ROOT

SNAPSHOT_RETENTION_DAYS = 30   # today plus the previous 29 days are kept


def cleanup_old_snapshots(root=None, today=None, retention_days=SNAPSHOT_RETENTION_DAYS):
    """Delete snapshot day folders older than the retention period. Returns the deleted paths."""
    root = root or SNAPSHOT_ROOT
    today = today or datetime.now().date()
    keep_from = today - timedelta(days=retention_days - 1)
    deleted = []

    # If the recordings disk is not mounted the folder is missing: do nothing.
    if not root.is_dir():
        return deleted

    for camera_dir in root.iterdir():
        if not camera_dir.is_dir() or camera_dir.is_symlink():
            continue

        for day_dir in camera_dir.iterdir():
            if not day_dir.is_dir() or day_dir.is_symlink():
                continue

            try:
                day = datetime.strptime(day_dir.name, "%Y-%m-%d").date()
            except ValueError:
                continue   # not a date folder: leave it alone

            if day < keep_from:
                shutil.rmtree(day_dir)
                deleted.append(str(day_dir))
                print(f"Snapshot cleanup deleted: {day_dir}")

    return deleted

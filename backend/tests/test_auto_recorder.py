import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import auto_recorder


class RecordingCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.internal_root = self.root / "internal" / "cameras"
        self.wd_root = self.root / "wd" / "cameras"
        self.internal_root.mkdir(parents=True)
        self.wd_root.mkdir(parents=True)
        self.snapshots_root = self.root / "snapshots"
        self.snapshots_root.mkdir()
        self.root_patch = patch.object(auto_recorder, "RECORDINGS_ROOT", self.wd_root)
        self.snapshots_patch = patch.object(auto_recorder, "SNAPSHOT_ROOT", self.snapshots_root)
        self.storage_patch = patch.object(auto_recorder, "ensure_recording_storage")
        self.root_patch.start()
        self.snapshots_patch.start()
        self.storage_patch.start()

    def tearDown(self):
        self.storage_patch.stop()
        self.snapshots_patch.stop()
        self.root_patch.stop()
        self.temp_dir.cleanup()

    def _create_day(self, camera_dir, recorded_on):
        day_dir = camera_dir / recorded_on.isoformat()
        day_dir.mkdir()
        (day_dir / "segment.mp4").write_bytes(b"recording")
        return day_dir

    def _create_snapshot_day(self, camera_id, recorded_on):
        day_dir = self.snapshots_root / camera_id / recorded_on.isoformat()
        day_dir.mkdir(parents=True)
        (day_dir / "snapshot.jpg").write_bytes(b"snapshot")
        return day_dir

    def test_emergency_cleanup_deletes_matching_snapshots(self):
        old_date = date.today() - timedelta(days=12)
        internal_camera = self.internal_root / "cam001"
        internal_camera.mkdir()
        internal_day = self._create_day(internal_camera, old_date)
        wd_camera = self.wd_root / "cam005"
        wd_camera.mkdir()
        wd_day = self._create_day(wd_camera, old_date)
        active_wd_day = self._create_day(wd_camera, date.today())
        old_snapshot_day = self._create_snapshot_day("cam005", old_date)
        active_snapshot_day = self._create_snapshot_day("cam005", date.today())

        result = auto_recorder.cleanup_oldest_recording_day()

        self.assertFalse(wd_day.exists())
        self.assertFalse(old_snapshot_day.exists())
        self.assertTrue(internal_day.exists())
        self.assertTrue(active_wd_day.exists())
        self.assertTrue(active_snapshot_day.exists())
        self.assertIn("WD", result)

    def test_age_retention_deletes_matching_snapshots_only_for_expired_days(self):
        old_date = date.today() - timedelta(days=auto_recorder.RETENTION_DAYS + 1)
        recent_date = date.today()
        internal_camera = self.internal_root / "cam001"
        internal_camera.mkdir()
        old_internal = self._create_day(internal_camera, old_date)
        wd_camera = self.wd_root / "cam005"
        wd_camera.mkdir()
        old_wd = self._create_day(wd_camera, old_date)
        recent_wd = self._create_day(wd_camera, recent_date)
        old_snapshot_day = self._create_snapshot_day("cam005", old_date)
        recent_snapshot_day = self._create_snapshot_day("cam005", recent_date)
        internal_snapshot_day = self._create_snapshot_day("cam001", old_date)

        deleted = auto_recorder.cleanup_old_recordings()

        self.assertTrue(old_internal.exists())
        self.assertFalse(old_wd.exists())
        self.assertFalse(old_snapshot_day.exists())
        self.assertTrue(recent_wd.exists())
        self.assertTrue(recent_snapshot_day.exists())
        self.assertTrue(internal_snapshot_day.exists())
        self.assertEqual(len(deleted), 1)


if __name__ == "__main__":
    unittest.main()
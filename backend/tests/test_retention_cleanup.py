import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

import snapshot_cleanup


class SnapshotCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.today = date(2026, 11, 1)

    def tearDown(self):
        self.tmp.cleanup()

    def _make(self, camera, day):
        d = self.root / camera / day
        d.mkdir(parents=True)
        (d / "10-00-00.jpg").write_bytes(b"x")
        return d

    def test_keeps_exactly_thirty_days_and_deletes_older(self):
        keep_first = (self.today - timedelta(days=29)).isoformat()     # 2026-10-03 -> kept
        delete_last = (self.today - timedelta(days=30)).isoformat()    # 2026-10-02 -> deleted
        for cam in ("cam001", "cam002"):
            self._make(cam, keep_first)
            self._make(cam, delete_last)
            self._make(cam, "2026-09-21")
            self._make(cam, self.today.isoformat())
        deleted = snapshot_cleanup.cleanup_old_snapshots(self.root, self.today)
        self.assertEqual(len(deleted), 4)   # 2 cameras x (delete_last + 2026-09-21)
        for cam in ("cam001", "cam002"):
            self.assertTrue((self.root / cam / keep_first).is_dir())
            self.assertTrue((self.root / cam / self.today.isoformat()).is_dir())
            self.assertFalse((self.root / cam / delete_last).exists())
            self.assertFalse((self.root / cam / "2026-09-21").exists())

    def test_ignores_non_date_folders_and_files(self):
        (self.root / "cam001").mkdir()
        (self.root / "cam001" / "notes").mkdir()
        (self.root / "cam001" / "readme.txt").write_text("keep")
        (self.root / "stray.txt").write_text("keep")
        self.assertEqual(snapshot_cleanup.cleanup_old_snapshots(self.root, self.today), [])
        self.assertTrue((self.root / "cam001" / "notes").is_dir())
        self.assertTrue((self.root / "stray.txt").exists())

    def test_missing_root_does_nothing(self):
        self.assertEqual(snapshot_cleanup.cleanup_old_snapshots(self.root / "not-mounted", self.today), [])


class MotionEventCleanupTests(unittest.TestCase):
    def test_deletes_only_events_older_than_retention(self):
        import database
        tmp = tempfile.TemporaryDirectory()
        old_path, old_keep = database.DB_PATH, database._keepalive_conn
        database.DB_PATH = Path(tmp.name) / "test.db"      # never touch the real database
        database._keepalive_conn = None
        try:
            database.init_db()
            conn = database.get_connection()
            now = datetime.now()
            rows = [
                ("cam001", (now - timedelta(days=45)).isoformat(), (now - timedelta(days=45)).isoformat()),
                ("cam001", (now - timedelta(days=31)).isoformat(), (now - timedelta(days=31)).isoformat()),
                ("cam001", (now - timedelta(days=29)).isoformat(), (now - timedelta(days=29)).isoformat()),
                ("cam002", now.isoformat(), now.isoformat()),
            ]
            conn.executemany("INSERT INTO motion_events (camera_id, started_at, created_at) VALUES (?,?,?)", rows)
            conn.commit(); conn.close()
            self.assertEqual(database.cleanup_old_motion_events(), 2)
            conn = database.get_connection()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM motion_events").fetchone()[0], 2)
            conn.close()
        finally:
            if database._keepalive_conn is not None:
                database._keepalive_conn.close()
            database.DB_PATH, database._keepalive_conn = old_path, old_keep
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()

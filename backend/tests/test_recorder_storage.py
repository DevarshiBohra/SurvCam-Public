import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import recorder
import recording_storage


class CameraStorageRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.wd_mount = self.root / "wd"
        self.wd_root = self.wd_mount / "cameras"
        self.wd_mount.mkdir()
        self.patches = [
            patch.object(recording_storage, "WD_MOUNT", self.wd_mount),
            patch.object(recording_storage, "RECORDINGS_ROOT", self.wd_root),
            patch("recording_storage.ensure_recording_storage"),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp_dir.cleanup()

    def test_all_camera_ids_route_to_wd_without_internal_fallback(self):
        for camera_id in ("cam001", "cam004", "cam005", "cam012"):
            self.assertEqual(
                recorder.camera_root_dir(camera_id),
                self.wd_root / camera_id,
            )

    def test_missing_wd_mount_fails_without_creating_internal_camera_dir(self):
        with patch(
            "recording_storage.ensure_recording_storage",
            side_effect=recording_storage.RecordingStorageUnavailable("not mounted"),
        ):
            with self.assertRaisesRegex(RuntimeError, "not mounted"):
                recorder.camera_root_dir("cam005")

        self.assertFalse((self.wd_root / "cam005").exists())

    def test_invalid_camera_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid camera ID"):
            recorder.camera_root_dir("../cam001")


class RecordingStorageMountValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.mount = self.root / "mount"
        self.mount.mkdir()
        self.device = self.root / "device"
        self.device.touch()
        self.patches = [
            patch.object(recording_storage, "WD_MOUNT", self.mount),
            patch.object(recording_storage, "WD_DEVICE_LINK", self.device),
            patch("recording_storage.os.path.ismount", return_value=True),
            patch("recording_storage.os.statvfs"),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp_dir.cleanup()

    def test_accepts_expected_device_mounted_at_recording_path(self):
        with patch.object(recording_storage, "_mounted_source", return_value=self.device):
            recording_storage.ensure_recording_storage()

    def test_rejects_a_different_device_at_recording_path(self):
        other_device = self.root / "other-device"
        other_device.touch()
        with patch.object(recording_storage, "_mounted_source", return_value=other_device):
            with self.assertRaisesRegex(
                recording_storage.RecordingStorageUnavailable,
                "Expected WD disk is not mounted",
            ):
                recording_storage.ensure_recording_storage()

    def test_rejects_missing_device_uuid(self):
        self.device.unlink()
        with self.assertRaisesRegex(
            recording_storage.RecordingStorageUnavailable,
            "UUID .* is not present",
        ):
            recording_storage.ensure_recording_storage()

    def test_automount_placeholder_does_not_hide_real_filesystem_mount(self):
        mounts = (
            f"systemd-1 {self.mount} autofs rw,relatime 0 0\n"
            f"{self.device} {self.mount} ext4 rw,relatime 0 0\n"
        )
        with patch.object(Path, "read_text", return_value=mounts):
            self.assertEqual(
                recording_storage._mounted_source(self.mount),
                self.device,
            )


if __name__ == "__main__":
    unittest.main()
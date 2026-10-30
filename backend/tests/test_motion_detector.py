import unittest

import numpy as np

import motion_detector
from unittest.mock import patch


class MotionDetectorRegionTests(unittest.TestCase):
    def test_bottom_half_cameras_ignore_changes_in_upper_half(self):
        baseline = np.zeros((480, 640, 3), dtype=np.uint8)
        upper_changed = baseline.copy()
        upper_changed[:240] = 255
        lower_changed = baseline.copy()
        lower_changed[240:] = 255

        with patch.object(motion_detector, "MOTION_BOTTOM_HALF_CAMERA_IDS", {"cam009", "cam010"}):
            for camera_id in ("cam009", "cam010"):
                with self.subTest(camera_id=camera_id):
                    prepared = motion_detector._prepare_frame(baseline, camera_id)
                    self.assertEqual(prepared.shape, (120, 320))
                    self.assertTrue(np.array_equal(
                        prepared,
                        motion_detector._prepare_frame(upper_changed, camera_id),
                    ))
                    self.assertFalse(np.array_equal(
                        prepared,
                        motion_detector._prepare_frame(lower_changed, camera_id),
                    ))

    def test_other_cameras_keep_full_frame_detection(self):
        baseline = np.zeros((480, 640, 3), dtype=np.uint8)
        upper_changed = baseline.copy()
        upper_changed[:240] = 255

        prepared = motion_detector._prepare_frame(baseline, "cam001")

        self.assertEqual(prepared.shape, (240, 320))
        self.assertFalse(np.array_equal(
            prepared,
            motion_detector._prepare_frame(upper_changed, "cam001"),
        ))


if __name__ == "__main__":
    unittest.main()
import time
import unittest

import stream


class StreamFpsTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(stream._streams)
        stream._streams.clear()

    def tearDown(self):
        stream._streams.clear()
        stream._streams.update(self._saved)

    def _state_with_frames(self, camera_id, per_second, seconds=3.0):
        state = stream.CameraStreamState(camera_id=camera_id)
        now = time.monotonic()
        total = int(per_second * seconds)
        for i in range(total):
            state.frame_times.append(now - seconds + (i + 1) * (seconds / total))
        stream._streams[camera_id] = state
        return state

    def test_unknown_camera_has_no_fps(self):
        self.assertIsNone(stream.get_fps("cam999"))

    def test_steady_ten_fps(self):
        self._state_with_frames("cam001", 10)
        self.assertEqual(stream.get_fps("cam001"), 10)

    def test_stalled_camera_drops_to_zero(self):
        state = stream.CameraStreamState(camera_id="cam002")
        state.frame_times.append(time.monotonic() - 10)
        stream._streams["cam002"] = state
        self.assertEqual(stream.get_fps("cam002"), 0)

    def test_all_fps_lists_every_known_camera(self):
        self._state_with_frames("cam001", 12)
        self._state_with_frames("cam002", 5)
        self.assertEqual(stream.get_all_fps(), {"cam001": 12, "cam002": 5})


if __name__ == "__main__":
    unittest.main()

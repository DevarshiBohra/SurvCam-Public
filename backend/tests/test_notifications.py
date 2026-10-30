import unittest
from unittest.mock import patch

from notifications import send_notification


class CameraNotificationFilteringTests(unittest.TestCase):
    @patch("notifications.add_notification_history")
    def test_ignores_notifications_for_cam011(self, add_notification_history):
        with patch("notifications._firebase_app", object()), patch("notifications.IGNORED_CAMERA_NOTIFICATION_IDS", {"cam011"}):
            send_notification(
                notif_type="camera_down",
                title="Camera is offline",
                body="Camera went offline",
                camera_id="cam011",
            )

        add_notification_history.assert_not_called()

    @patch("notifications.add_notification_history")
    def test_does_not_ignore_other_cameras(self, add_notification_history):
        with patch("notifications._firebase_app", None):
            send_notification(
                notif_type="camera_down",
                title="Camera is offline",
                body="Camera went offline",
                camera_id="cam012",
            )

        add_notification_history.assert_called_once_with(
            "camera_down",
            "Camera is offline",
            "Camera went offline",
            "cam012",
        )


if __name__ == "__main__":
    unittest.main()

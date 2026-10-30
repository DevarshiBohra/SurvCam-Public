import unittest
from unittest.mock import patch

from recording_storage_monitor import (
    STABLE_CHECKS_REQUIRED,
    _advance_candidate_state,
    _notify_state_change,
)


class RecordingStorageNotificationTests(unittest.TestCase):
    @patch("recording_storage_monitor.send_notification")
    def test_notifies_once_when_drive_becomes_unmounted(self, send_notification):
        state = _notify_state_change(True, False)
        same_state = _notify_state_change(state, False)

        self.assertFalse(same_state)
        send_notification.assert_called_once_with(
            notif_type="external_recording_drive_unmounted",
            title="External recording drive unmounted",
            body="External recording drive unmounted, recordings will be stopped",
        )

    @patch("recording_storage_monitor.send_notification")
    def test_notifies_when_drive_becomes_mounted(self, send_notification):
        state = _notify_state_change(False, True)

        self.assertTrue(state)
        send_notification.assert_called_once_with(
            notif_type="external_recording_drive_mounted",
            title="External recording drive mounted",
            body="External recording drive mounted. Recordings will begin",
        )

    @patch("recording_storage_monitor.send_notification")
    def test_does_not_notify_for_mounted_startup(self, send_notification):
        self.assertTrue(_notify_state_change(None, True))
        send_notification.assert_not_called()

    @patch("recording_storage_monitor.send_notification")
    def test_notifies_if_service_starts_without_the_drive(self, send_notification):
        self.assertFalse(_notify_state_change(None, False))
        send_notification.assert_called_once_with(
            notif_type="external_recording_drive_unmounted",
            title="External recording drive unmounted",
            body="External recording drive unmounted, recordings will be stopped",
        )

    def test_state_transition_requires_consecutive_checks(self):
        self.assertEqual(STABLE_CHECKS_REQUIRED, 2)
        previous, candidate, checks, confirmed = _advance_candidate_state(
            True, None, 0, False
        )
        self.assertEqual((previous, candidate, checks, confirmed), (True, False, 1, False))

        previous, candidate, checks, confirmed = _advance_candidate_state(
            previous, candidate, checks, True
        )
        self.assertEqual((previous, candidate, checks, confirmed), (True, None, 0, False))

        previous, candidate, checks, confirmed = _advance_candidate_state(
            previous, candidate, checks, False
        )
        self.assertEqual((previous, candidate, checks, confirmed), (True, False, 1, False))

        previous, candidate, checks, confirmed = _advance_candidate_state(
            previous, candidate, checks, False
        )
        self.assertEqual((previous, candidate, checks, confirmed), (False, None, 0, True))


if __name__ == "__main__":
    unittest.main()
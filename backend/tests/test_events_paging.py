import importlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta


class EventsPagingTests(unittest.TestCase):
    """Builds a small telemetry database with known reboots and dropouts and checks the paging."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        os.environ["SURVCAM_TELEMETRY_DB"] = os.path.join(cls._tmp.name, "telemetry.db")
        import telemetry
        cls.telemetry = importlib.reload(telemetry)

        start = datetime.now() - timedelta(hours=10)
        t, uptime = start, 0
        cls.expected = []          # (type, "at") oldest first
        for i in range(4000):
            step = 5
            kind = None
            if i in (500, 1500, 2500, 3500):
                uptime = 0                      # reboot: uptime goes down
                kind = "reboot"
            elif i in (1000, 2000, 3000):
                step = 60                       # dropout: silence, uptime keeps counting
                uptime += 55_000
                kind = "dropout"
            prev_t = t
            t += timedelta(seconds=step)
            uptime += 5000
            cls.telemetry.record_telemetry(
                "cam001",
                {"status": "online", "uptime_ms": uptime, "fw_version": "0.11.0"},
                recorded_at=t.strftime(cls.telemetry.TS_FMT),
            )
            if kind:
                cls.expected.append((kind, prev_t.strftime(cls.telemetry.TS_FMT)))
        # another camera must never leak into cam001's list
        cls.telemetry.record_telemetry("cam002", {"status": "online", "uptime_ms": 1}, recorded_at=start.strftime(cls.telemetry.TS_FMT))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_finds_every_event_newest_first(self):
        events, more = self.telemetry.list_events_page("cam001", hours=24, limit=50)
        self.assertFalse(more)
        got = [(e["type"], e["at"]) for e in events]
        self.assertEqual(got, list(reversed(self.expected)))

    def test_paging_walks_all_events_without_gaps_or_repeats(self):
        collected, offset = [], 0
        while True:
            page, more = self.telemetry.list_events_page("cam001", hours=24, limit=3, offset=offset)
            collected += page
            offset += len(page)
            if not more:
                break
        self.assertEqual([(e["type"], e["at"]) for e in collected], list(reversed(self.expected)))

    def test_has_more_is_exact(self):
        total = len(self.expected)
        _, more = self.telemetry.list_events_page("cam001", hours=24, limit=total)
        self.assertFalse(more)
        _, more = self.telemetry.list_events_page("cam001", hours=24, limit=total - 1)
        self.assertTrue(more)

    def test_offset_past_the_end_is_empty(self):
        page, more = self.telemetry.list_events_page("cam001", hours=24, limit=10, offset=999)
        self.assertEqual((page, more), ([], False))

    def test_duration_and_type_fields(self):
        events, _ = self.telemetry.list_events_page("cam001", hours=24, limit=50)
        dropouts = [e for e in events if e["type"] == "dropout"]
        self.assertTrue(dropouts and all(e["duration_s"] == 60 for e in dropouts))
        self.assertTrue(all(e["restart_cause"] is None for e in dropouts))

    def test_old_list_events_still_works(self):
        self.assertEqual(len(self.telemetry.list_events("cam001", hours=24)), len(self.expected))


if __name__ == "__main__":
    unittest.main()

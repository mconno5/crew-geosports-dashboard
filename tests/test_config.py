from datetime import datetime, timezone
import unittest

from geosports.aggregate import build_dashboard_data
from geosports.config import player_display, player_id
from geosports.models import ScoreRow


CONFIG = {
    "players": {"mark": {"name": "Mark", "color": "#4a9ee8"}},
    "senders": {"Me": "mark", "3125550100": "mark"},
}

EMPTY_CONFIG = {"players": {}, "senders": {}}


class ConfigTests(unittest.TestCase):
    def test_multiple_senders_resolve_to_one_player(self):
        self.assertEqual(player_id("Me", CONFIG), "mark")
        self.assertEqual(player_id("+1 (312) 555-0100", CONFIG), "mark")

    def test_unmapped_sender_never_exposes_phone_number(self):
        pid = player_id("+18005551234", EMPTY_CONFIG)
        self.assertTrue(pid.startswith("player-"))
        self.assertNotIn("8005551234", pid)
        self.assertEqual(pid, player_id("8005551234", EMPTY_CONFIG))

    def test_unmapped_player_display_name_is_not_a_phone_number(self):
        pid = player_id("+18005551234", EMPTY_CONFIG)
        display = player_display(pid, EMPTY_CONFIG, 0)
        self.assertNotIn("8005551234", display["name"])


class AggregateTests(unittest.TestCase):
    def test_senders_merge_and_same_day_duplicate_is_dropped(self):
        day1 = datetime(2026, 6, 2, 10, tzinfo=timezone.utc)
        day2 = datetime(2026, 6, 3, 10, tzinfo=timezone.utc)
        rows = [
            ScoreRow(day1, "Me", 700),
            ScoreRow(day1.replace(hour=12), "3125550100", 800),
            ScoreRow(day2, "3125550100", 650),
        ]
        data = build_dashboard_data(rows, CONFIG)
        self.assertEqual(len(data["players"]), 1)
        player = data["players"][0]
        self.assertEqual(player["id"], "mark")
        self.assertEqual(player["count"], 2)
        self.assertEqual(data["dailyScores"]["mark"], [700, 650])
        self.assertEqual(data["meta"]["scoreCount"], 2)

    def test_output_contains_no_raw_sender_ids(self):
        rows = [ScoreRow(datetime(2026, 6, 3, tzinfo=timezone.utc), "3125550199", 731)]
        data = build_dashboard_data(rows, EMPTY_CONFIG)
        self.assertNotIn("3125550199", str(data))

    def test_recent_form_window_is_shared_and_anchored_to_latest_score(self):
        rows = [
            ScoreRow(datetime(2026, 5, 20, 10, tzinfo=timezone.utc), "Me", 700),
            ScoreRow(datetime(2026, 6, 3, 10, tzinfo=timezone.utc), "3125550100", 800),
        ]
        window = build_dashboard_data(rows, CONFIG)["meta"]["recentFormWindow"]
        self.assertEqual(window["days"], 7)
        self.assertEqual(window["startDate"], "2026-05-28")
        self.assertEqual(window["endDate"], "2026-06-03")


if __name__ == "__main__":
    unittest.main()

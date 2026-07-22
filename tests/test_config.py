from datetime import datetime, timezone
import unittest

from geosports.aggregate import build_dashboard_data
from geosports.config import player_display, player_id
from geosports.models import ScoreRow


CONFIG = {
    "players": {
        "mark": {"name": "Mark", "color": "#4a9ee8"},
        "sam": {"name": "Sam", "color": "#de6b48"},
    },
    "senders": {"Me": "mark", "3125550100": "mark", "3125550101": "sam"},
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

    def test_same_day_same_score_different_players_survive(self):
        rows = [
            ScoreRow(datetime(2026, 6, 21, 10, tzinfo=timezone.utc), "Me", 700),
            ScoreRow(datetime(2026, 6, 21, 11, tzinfo=timezone.utc), "3125550101", 700),
        ]
        data = build_dashboard_data(rows, CONFIG)
        self.assertEqual(data["dailyScores"]["mark"], [700])
        self.assertEqual(data["dailyScores"]["sam"], [700])
        self.assertEqual(data["meta"]["scoreCount"], 2)

    def test_question_stats_count_green_by_position_and_skip_missing_slots(self):
        rows = [
            ScoreRow(datetime(2026, 6, 21, 10, tzinfo=timezone.utc), "Me", 700, "🟢🟡🔴🟢🟡"),
            ScoreRow(datetime(2026, 6, 22, 10, tzinfo=timezone.utc), "Me", 710, "🟡🟢🟢"),
        ]
        stats = build_dashboard_data(rows, CONFIG)["questionStats"]["mark"]
        self.assertEqual(stats[0], {"question": 1, "green": 1, "attempts": 2, "greenRate": 50})
        self.assertEqual(stats[1], {"question": 2, "green": 1, "attempts": 2, "greenRate": 50})
        self.assertEqual(stats[2], {"question": 3, "green": 1, "attempts": 2, "greenRate": 50})
        self.assertEqual(stats[3], {"question": 4, "green": 1, "attempts": 1, "greenRate": 100})
        self.assertEqual(stats[4], {"question": 5, "green": 0, "attempts": 1, "greenRate": 0})


if __name__ == "__main__":
    unittest.main()

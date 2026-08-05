import tempfile
from datetime import datetime, timezone
from pathlib import Path
import unittest

from geosports.aggregate import build_dashboard_data
from geosports.backfill import BackfillValidationError, merge_score_sources, read_backfill_scores
from geosports.models import ScoreRow


CONFIG = {
    "players": {
        "mark": {"name": "Mark", "color": "#4a9ee8"},
        "sanup": {"name": "Sanup", "color": "#4ae8a0"},
    },
    "senders": {"Me": "mark", "3125550100": "sanup"},
}


class BackfillTests(unittest.TestCase):
    def write_backfill(self, contents: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "geosports_backfill.local.csv"
        path.write_text(contents, encoding="utf-8")
        return path

    def test_reference_only_row_flows_into_dashboard_and_question_stats(self):
        path = self.write_backfill(
            "date,player_id,score,emoji_row,source_image\n"
            "2026-07-17,mark,848,🟢🟡🟡🟢🟡,IMG_1852.PNG\n"
        )
        rows = read_backfill_scores(path, CONFIG)
        result = merge_score_sources([], rows, CONFIG)
        data = build_dashboard_data(result.rows, CONFIG)

        self.assertEqual(result.backfill_accepted, 1)
        self.assertEqual(data["dailyScores"]["mark"], [848])
        self.assertEqual(data["questionStats"]["mark"][0]["greenRate"], 100)

    def test_messages_win_same_player_day_and_conflicts_are_reported_without_handles(self):
        path = self.write_backfill(
            "date,player_id,score,emoji_row\n"
            "2026-07-17,sanup,848,🟢🟡🟡🟢🟡\n"
        )
        backfill = read_backfill_scores(path, CONFIG)
        messages = [ScoreRow(datetime(2026, 7, 17, 10, tzinfo=timezone.utc), "3125550100", 849, "🟢🟢🟡🟢🟡")]
        result = merge_score_sources(messages, backfill, CONFIG)

        self.assertEqual([(row.sender, row.score) for row in result.rows], [("3125550100", 849)])
        self.assertEqual(result.collisions_skipped, 1)
        self.assertEqual(result.conflicts, ["2026-07-17 player=sanup"])
        self.assertNotIn("3125550100", " ".join(result.conflicts))

    def test_same_score_for_different_players_is_kept(self):
        path = self.write_backfill(
            "date,player_id,score,emoji_row\n"
            "2026-07-17,mark,700,🟢🟡🟡🟢🟡\n"
            "2026-07-17,sanup,700,🟢🟡🟡🟢🟡\n"
        )
        result = merge_score_sources([], read_backfill_scores(path, CONFIG), CONFIG)

        self.assertEqual(len(result.rows), 2)

    def test_reference_accepts_trophy_answer_marker(self):
        path = self.write_backfill(
            "date,player_id,score,emoji_row\n"
            "2026-08-05,mark,771,🟢🏆🟡🟢🔴\n"
        )

        rows = read_backfill_scores(path, CONFIG)
        self.assertEqual(rows[0].emoji_row, "🟢🏆🟡🟢🔴")

    def test_duplicate_reference_player_day_is_rejected(self):
        path = self.write_backfill(
            "date,player_id,score,emoji_row\n"
            "2026-07-17,mark,700,🟢🟡🟡🟢🟡\n"
            "2026-07-17,mark,701,🟢🟡🟡🟢🟡\n"
        )

        with self.assertRaises(BackfillValidationError):
            read_backfill_scores(path, CONFIG)


if __name__ == "__main__":
    unittest.main()

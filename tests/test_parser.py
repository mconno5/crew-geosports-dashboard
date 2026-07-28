from datetime import datetime, timezone
import unittest

from geosports.models import RawMessage, ScoreRow
from geosports.parser import dedupe_scores, parse_messages, parse_score


class ParserTests(unittest.TestCase):
    def test_parse_score_accepts_commas_and_spacing(self):
        self.assertEqual(parse_score("GeoSports 702 / 1,000 🟢🟡🔴"), (702, "🟢🟡🔴"))

    def test_parse_messages_filters_non_scores(self):
        rows = parse_messages(
            [
                RawMessage(datetime(2026, 6, 3, tzinfo=timezone.utc), "+13125550100", "GeoSports 725 / 1,000"),
                RawMessage(datetime(2026, 6, 3, tzinfo=timezone.utc), "+13125550100", "not a score"),
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].sender, "3125550100")
        self.assertEqual(rows[0].score, 725)

    def test_parse_messages_rejects_other_score_shaped_games(self):
        rows = parse_messages(
            [
                RawMessage(datetime(2026, 7, 28, tzinfo=timezone.utc), "+13125550100", "GeoHistory 674 / 1,000 🟢🟢🟡🟡🔴"),
                RawMessage(datetime(2026, 7, 28, tzinfo=timezone.utc), "+13125550101", "GEOSPORTS 725 / 1,000 🟢🟡🟡🟢🟡"),
            ]
        )
        self.assertEqual([(row.sender, row.score) for row in rows], [("3125550101", 725)])

    def test_parse_messages_filters_replies(self):
        rows = parse_messages(
            [
                RawMessage(datetime(2026, 6, 22, tzinfo=timezone.utc), "+13125550100", "GeoSports 725 / 1,000", True),
                RawMessage(datetime(2026, 6, 22, tzinfo=timezone.utc), "+13125550101", "GeoSports 726 / 1,000"),
            ]
        )
        self.assertEqual([(r.sender, r.score) for r in rows], [("3125550101", 726)])

    def test_dedupe_keeps_first_sender_and_old_first_score_rule_before_tie_cutoff(self):
        day = datetime(2026, 6, 3, tzinfo=timezone.utc)
        rows = dedupe_scores(
            [
                ScoreRow(day.replace(hour=10), "a", 700),
                ScoreRow(day.replace(hour=11), "a", 800),
                ScoreRow(day.replace(hour=12), "b", 700),
                ScoreRow(day.replace(hour=13), "b", 801),
            ]
        )
        self.assertEqual([(r.sender, r.score) for r in rows], [("a", 700)])

    def test_dedupe_allows_same_score_for_different_senders_after_tie_cutoff(self):
        day = datetime(2026, 6, 21, tzinfo=timezone.utc)
        rows = dedupe_scores(
            [
                ScoreRow(day.replace(hour=10), "a", 700),
                ScoreRow(day.replace(hour=11), "a", 800),
                ScoreRow(day.replace(hour=12), "b", 700),
                ScoreRow(day.replace(hour=13), "b", 801),
            ]
        )
        self.assertEqual([(r.sender, r.score) for r in rows], [("a", 700), ("b", 700)])

    def test_dedupe_keeps_old_duplicate_score_rule_on_cutoff_day(self):
        day = datetime(2026, 6, 20, tzinfo=timezone.utc)
        rows = dedupe_scores(
            [
                ScoreRow(day.replace(hour=10), "a", 700),
                ScoreRow(day.replace(hour=12), "b", 700),
            ]
        )
        self.assertEqual([(r.sender, r.score) for r in rows], [("a", 700)])


if __name__ == "__main__":
    unittest.main()

import csv
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import unittest

from geosports.archive import (
    ArchiveValidationError,
    read_archive_scores,
    sync_archive,
    write_archive,
)
from geosports.models import ScoreRow


CONFIG = {
    "players": {
        "mark": {"name": "Mark", "color": "#4a9ee8"},
        "sanup": {"name": "Sanup", "color": "#4ae8a0"},
    },
    "senders": {"Me": "mark", "3125550100": "sanup"},
}


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.archive_path = Path(self.temp_dir.name) / "geosports_history.local.csv"
        self.manifest_path = Path(self.temp_dir.name) / "geosports_history_manifest.local.json"

    def score(self, day: int, sender: str, score: int, emoji_row="🟢🟡🟡🟢🟡"):
        return ScoreRow(datetime(2026, 7, day, 10, tzinfo=timezone.utc), sender, score, emoji_row)

    def test_initial_snapshot_preserves_messages_and_backfill_with_messages_first(self):
        messages = [self.score(17, "Me", 700)]
        backfill = [
            self.score(17, "Me", 701),
            self.score(17, "3125550100", 700),
        ]

        result = sync_archive([], messages, backfill, CONFIG)

        self.assertEqual([(row.sender, row.score) for row in result.rows], [("mark", 700), ("sanup", 700)])
        self.assertEqual(result.messages_added, 1)
        self.assertEqual(result.backfill_added, 1)
        self.assertEqual(result.conflicts, ["2026-07-17 player=mark source=backfill"])

    def test_existing_archive_is_retained_when_live_messages_disagree(self):
        archive_rows = [self.score(17, "mark", 700)]
        messages = [self.score(17, "Me", 701)]

        result = sync_archive(archive_rows, messages, [], CONFIG)

        self.assertEqual([(row.sender, row.score) for row in result.rows], [("mark", 700)])
        self.assertEqual(result.messages_added, 0)
        self.assertEqual(result.conflicts, ["2026-07-17 player=mark source=messages"])

    def test_new_message_is_appended_and_ties_are_kept(self):
        archive_rows = [self.score(17, "mark", 700)]
        messages = [self.score(18, "Me", 800), self.score(18, "3125550100", 800)]

        result = sync_archive(archive_rows, messages, [], CONFIG)

        self.assertEqual(len(result.rows), 3)
        self.assertEqual(result.messages_added, 2)
        self.assertEqual([(row.sender, row.score) for row in result.rows[-2:]], [("mark", 800), ("sanup", 800)])

    def test_archive_write_uses_public_player_ids_and_manifest_checksum(self):
        rows = [self.score(17, "Me", 700), self.score(17, "3125550100", 800)]

        write_archive(self.archive_path, self.manifest_path, rows, CONFIG)

        contents = self.archive_path.read_text(encoding="utf-8")
        self.assertIn("player_id", contents)
        self.assertIn(",mark,700,", contents)
        self.assertIn(",sanup,800,", contents)
        self.assertNotIn("Me", contents)
        self.assertNotIn("3125550100", contents)
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["scoreCount"], 2)
        self.assertEqual(manifest["dateRange"], {"start": "2026-07-17", "end": "2026-07-17"})
        self.assertEqual(read_archive_scores(self.archive_path, CONFIG)[0].sender, "mark")

    def test_duplicate_player_date_is_rejected(self):
        with self.archive_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["date", "player_id", "score", "emoji_row"])
            writer.writerow(["2026-07-17", "mark", 700, "🟢🟡🟡🟢🟡"])
            writer.writerow(["2026-07-17", "mark", 701, "🟢🟡🟡🟢🟡"])

        with self.assertRaises(ArchiveValidationError):
            read_archive_scores(self.archive_path, CONFIG)

    def test_partial_or_blank_answer_markers_are_preserved_for_older_valid_shares(self):
        with self.archive_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(["date", "player_id", "score", "emoji_row"])
            writer.writerow(["2026-07-17", "mark", 700, ""])
            writer.writerow(["2026-07-18", "mark", 701, "🟢🟡🟢"])

        rows = read_archive_scores(self.archive_path, CONFIG)
        self.assertEqual(rows[0].emoji_row, "")
        self.assertEqual(rows[1].emoji_row, "🟢🟡🟢")


if __name__ == "__main__":
    unittest.main()

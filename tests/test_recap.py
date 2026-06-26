from __future__ import annotations

import argparse
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from geosports.recap import (
    build_fact_pack,
    default_paths,
    fallback_recap,
    missing_approval_config,
    poll_approvals,
    save_state,
    should_draft,
)


SAMPLE_DATA = {
    "meta": {
        "scoreCount": 6,
        "recentFormWindow": {
            "days": 7,
            "startDate": "2026-06-19",
            "endDate": "2026-06-25",
            "label": "June 19 - 25, 2026",
        },
    },
    "players": [
        {"id": "mark", "name": "Mark", "avg": 700},
        {"id": "sanup", "name": "Sanup", "avg": 820},
        {"id": "casey", "name": "Casey", "avg": 710},
    ],
    "dates": ["06-19", "06-20", "06-21", "06-22", "06-23", "06-24", "06-25"],
    "dailyScores": {
        "mark": [700, None, 740, 750, None, 760, 770],
        "sanup": [830, 840, 850, 860, 870, 880, 890],
        "casey": [None, None, None, None, None, None, 900],
    },
}


class RecapTests(unittest.TestCase):
    def test_should_draft_on_monday_wednesday_saturday_only(self):
        state = {}
        self.assertTrue(should_draft(SAMPLE_DATA, state, date(2026, 6, 22))[0])
        self.assertTrue(should_draft(SAMPLE_DATA, state, date(2026, 6, 24))[0])
        self.assertTrue(should_draft(SAMPLE_DATA, state, date(2026, 6, 27))[0])
        self.assertFalse(should_draft(SAMPLE_DATA, {"last_due_day": "2026-06-24"}, date(2026, 6, 26))[0])

    def test_should_draft_catches_up_missed_due_day(self):
        ok, reason = should_draft(SAMPLE_DATA, {}, date(2026, 6, 26))
        self.assertTrue(ok, reason)

    def test_should_not_draft_without_new_scores(self):
        state = {
            "last_drafted_score_count": 6,
            "last_drafted_latest_score_date": "2026-06-25",
        }
        ok, reason = should_draft(SAMPLE_DATA, state, date(2026, 6, 25))
        self.assertFalse(ok)
        self.assertIn("no new scores", reason)

    def test_fact_pack_uses_latest_date_and_recent_window(self):
        facts = build_fact_pack(SAMPLE_DATA)
        self.assertEqual(facts["latest_score_date"], "2026-06-25")
        self.assertEqual(facts["recent_window"]["label"], "June 19 - 25, 2026")
        self.assertEqual(facts["latest_winner"], {"name": "Casey", "score": 900})
        self.assertEqual(facts["recent_leader"]["name"], "Sanup")
        self.assertNotIn("312555", str(facts))

    def test_fallback_recap_includes_site_link(self):
        text = fallback_recap(build_fact_pack(SAMPLE_DATA))
        self.assertIn("https://mconno5.github.io/crew-geosports-dashboard/", text)
        self.assertLessEqual(len(text), 900)

    def test_missing_approval_config_requires_github_mailbox(self):
        self.assertEqual(
            missing_approval_config({"GITHUB_REPO": "mconno5/crew-geosports-dashboard"}),
            ["GITHUB_TOKEN", "GITHUB_APPROVAL_ISSUE_NUMBER"],
        )

    def test_poll_approval_sends_matching_token_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = default_paths(root)
            paths.recaps_dir.mkdir(parents=True)
            paths.latest_txt.write_text("Draft text", encoding="utf-8")
            save_state(
                paths.state_json,
                {
                    "draft": {
                        "token": "abc123",
                        "created_at": "2026-06-25T12:00:00+00:00",
                        "expires_at": "2999-01-01T00:00:00+00:00",
                        "sent_at": None,
                    }
                },
            )

            with (
                patch("geosports.recap.default_paths", return_value=paths),
                patch("geosports.recap.recap_config", return_value={}),
                patch(
                    "geosports.recap.approval_comments",
                    return_value=[
                        {
                            "id": 42,
                            "body": "/send abc123",
                            "created_at": "2026-06-25T12:01:00+00:00",
                        }
                    ],
                ),
                patch("geosports.recap.send_message") as send_message,
            ):
                poll_approvals(argparse.Namespace())

            send_message.assert_called_once_with("Draft text")


if __name__ == "__main__":
    unittest.main()

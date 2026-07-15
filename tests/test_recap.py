from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

from geosports.recap import (
    build_fact_pack, default_paths, draft_recap, draft_status, fallback_recap,
    load_state, poll_approvals, save_state, should_draft, status_recap,
)

SAMPLE_DATA = {
    "meta": {"scoreCount": 6, "recentFormWindow": {"days": 7, "startDate": "2026-06-19", "endDate": "2026-06-25", "label": "June 19 - 25, 2026"}},
    "players": [{"id": "mark", "name": "Mark", "avg": 700}, {"id": "sanup", "name": "Sanup", "avg": 820}, {"id": "casey", "name": "Casey", "avg": 710}],
    "dates": ["06-19", "06-20", "06-21", "06-22", "06-23", "06-24", "06-25"],
    "dailyScores": {"mark": [700, None, 740, 750, None, 760, 770], "sanup": [830, 840, 850, 860, 870, 880, 890], "casey": [None, None, None, None, None, None, 900]},
}


class RecapTests(unittest.TestCase):
    def test_drafts_only_monday_wednesday_friday_sunday(self):
        for day in (date(2026, 6, 22), date(2026, 6, 24), date(2026, 6, 26), date(2026, 6, 28)):
            self.assertTrue(should_draft(SAMPLE_DATA, {}, day)[0])
        self.assertFalse(should_draft(SAMPLE_DATA, {}, date(2026, 6, 23))[0])

    def test_no_missed_day_catchup(self):
        ok, reason = should_draft(SAMPLE_DATA, {}, date(2026, 6, 23))
        self.assertFalse(ok)
        self.assertEqual(reason, "not a recap day")

    def test_same_data_does_not_redraft(self):
        state = {"last_drafted_score_count": 6, "last_drafted_latest_score_date": "2026-06-25"}
        self.assertFalse(should_draft(SAMPLE_DATA, state, date(2026, 6, 26))[0])

    def test_pending_blocks_and_closed_drafts_do_not(self):
        self.assertFalse(should_draft(SAMPLE_DATA, {"draft": {"token": "a"}}, date(2026, 6, 26))[0])
        for field in ("sent_at", "discarded_at", "expired_at", "abandoned_at", "send_failed_at", "preview_send_failed_at"):
            self.assertTrue(should_draft(SAMPLE_DATA, {"draft": {"token": "a", field: "2026-01-01T00:00:00+00:00"}}, date(2026, 6, 26))[0])

    def test_fact_pack_and_fallback_handle_tie(self):
        tied = json.loads(json.dumps(SAMPLE_DATA))
        tied["dailyScores"]["mark"][-1] = 900
        facts = build_fact_pack(tied)
        self.assertTrue(facts["latest_is_tie"])
        self.assertEqual([x["name"] for x in facts["latest_winners"]], ["Mark", "Casey"])
        text = fallback_recap(facts)
        self.assertIn("Mark and Casey split the latest crown", text)
        self.assertIn("https://mconno5.github.io/crew-geosports-dashboard/", text)
        self.assertLessEqual(len(text), 450)

    def test_draft_sends_private_preview_and_migrates_legacy_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = default_paths(Path(tmp))
            paths.data_dir.mkdir(parents=True)
            paths.dashboard_json.write_text(json.dumps(SAMPLE_DATA), encoding="utf-8")
            save_state(paths.state_json, {"draft": {"token": "legacy", "github_status": "posted"}})
            with patch("geosports.recap.default_paths", return_value=paths), patch("geosports.recap.recap_config", return_value={"RECAP_APPROVAL_HANDLE": "+17084088254"}), patch("geosports.recap.openai_recap", return_value="Two short sentences.\nhttps://mconno5.github.io/crew-geosports-dashboard/"), patch("geosports.recap.send_preview_message") as send_preview, patch("geosports.recap.secrets.token_urlsafe", return_value="newtoken"):
                draft_recap(argparse.Namespace(if_due=True, force=True, replace_pending=True, run_date="2026-06-26"))
            draft = load_state(paths.state_json)["draft"]
            self.assertEqual(draft["approval_channel"], "imessage")
            self.assertEqual(draft_status(draft), "pending_review")
            send_preview.assert_called_once()
            self.assertIn("Reply APPROVE", send_preview.call_args.args[0])

    def test_approve_sends_once_and_skip_never_sends(self):
        for command, expected in (("approve", 1), ("skip", 0)):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                paths = default_paths(Path(tmp))
                paths.recaps_dir.mkdir(parents=True)
                paths.latest_txt.write_text("Exact recap", encoding="utf-8")
                save_state(paths.state_json, {"draft": {"token": "abc", "approval_channel": "imessage", "approval_handle": "+17084088254", "preview_sent_at": "2026-06-25T12:00:00+00:00", "expires_at": "2999-01-01T00:00:00+00:00", "send_attempt_count": 0}})
                reply = {"rowid": 42, "guid": "g", "timestamp": __import__("datetime").datetime(2026, 6, 25, 12, 1, tzinfo=__import__("datetime").timezone.utc), "text": command}
                with patch("geosports.recap.default_paths", return_value=paths), patch("geosports.recap.approval_messages", return_value=[reply]), patch("geosports.recap.send_message") as send:
                    poll_approvals(argparse.Namespace())
                self.assertEqual(send.call_count, expected)
                self.assertEqual(draft_status(load_state(paths.state_json)["draft"]), "sent" if command == "approve" else "discarded")

    def test_non_command_reply_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = default_paths(Path(tmp))
            paths.recaps_dir.mkdir(parents=True)
            paths.latest_txt.write_text("Exact recap", encoding="utf-8")
            save_state(paths.state_json, {"draft": {"token": "abc", "approval_channel": "imessage", "approval_handle": "+17084088254", "preview_sent_at": "2026-06-25T12:00:00+00:00", "expires_at": "2999-01-01T00:00:00+00:00"}})
            reply = {"rowid": 42, "guid": "g", "timestamp": __import__("datetime").datetime(2026, 6, 25, 12, 1, tzinfo=__import__("datetime").timezone.utc), "text": "looks good"}
            with patch("geosports.recap.default_paths", return_value=paths), patch("geosports.recap.approval_messages", return_value=[reply]), patch("geosports.recap.send_message") as send:
                poll_approvals(argparse.Namespace())
            send.assert_not_called()

    def test_status_reports_direct_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = default_paths(Path(tmp))
            paths.data_dir.mkdir(parents=True)
            paths.dashboard_json.write_text(json.dumps(SAMPLE_DATA), encoding="utf-8")
            save_state(paths.state_json, {"draft": {"token": "abc", "approval_channel": "imessage", "preview_sent_at": "2026-06-25T12:00:00+00:00"}})
            output = io.StringIO()
            with patch("geosports.recap.default_paths", return_value=paths), redirect_stdout(output):
                status_recap(argparse.Namespace())
            self.assertIn("Approval channel: imessage", output.getvalue())


if __name__ == "__main__":
    unittest.main()

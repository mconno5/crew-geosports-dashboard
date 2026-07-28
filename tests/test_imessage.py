import sqlite3
import tempfile
import unittest
from pathlib import Path

from geosports.imessage import fetch_messages, is_reply_or_reaction


class IMessageTests(unittest.TestCase):
    def test_is_reply_or_reaction_detects_associated_and_reply_metadata(self):
        self.assertFalse(is_reply_or_reaction(0, None, None))
        self.assertTrue(is_reply_or_reaction(2000, None, None))
        self.assertFalse(is_reply_or_reaction(0, "reply-guid", None))
        self.assertTrue(is_reply_or_reaction(0, None, "thread-guid"))

    def test_fetch_messages_keeps_score_posts_with_reply_to_guid_but_skips_reactions_and_inline_replies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "chat.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
                    CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
                    CREATE TABLE message (
                        ROWID INTEGER PRIMARY KEY,
                        date INTEGER,
                        handle_id INTEGER,
                        text TEXT,
                        attributedBody BLOB,
                        associated_message_type INTEGER,
                        reply_to_guid TEXT,
                        thread_originator_guid TEXT
                    );
                    """
                )
                conn.execute("INSERT INTO handle (ROWID, id) VALUES (1, '+13125550100')")
                rows = [
                    (1, 1, "+13125550100 GeoSports 700 / 1,000", 0, None, None),
                    (2, 2, "+13125550100 GeoSports 701 / 1,000", 2000, None, None),
                    (3, 3, "+13125550100 GeoSports 702 / 1,000", 0, "reply-guid", None),
                    (4, 4, "+13125550100 GeoSports 703 / 1,000", 0, None, "thread-guid"),
                    (5, 5, "+13125550100 GeoHistory 704 / 1,000", 0, None, None),
                ]
                for rowid, date, text, associated_type, reply_to_guid, thread_originator_guid in rows:
                    conn.execute(
                        """
                        INSERT INTO message
                            (ROWID, date, handle_id, text, associated_message_type, reply_to_guid, thread_originator_guid)
                        VALUES (?, ?, 1, ?, ?, ?, ?)
                        """,
                        (rowid, date, text, associated_type, reply_to_guid, thread_originator_guid),
                    )
                    conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (99, ?)", (rowid,))
                conn.commit()
            finally:
                conn.close()

            messages = fetch_messages(db_path, [99])

        self.assertEqual([message.message for message in messages], [
            "+13125550100 GeoSports 700 / 1,000",
            "+13125550100 GeoSports 702 / 1,000",
        ])


if __name__ == "__main__":
    unittest.main()

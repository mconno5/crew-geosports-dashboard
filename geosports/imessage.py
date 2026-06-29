from __future__ import annotations

import os
import plistlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import RawMessage
from .parser import SEARCH_TERMS

DB_PATH = Path(os.path.expanduser("~/Library/Messages/chat.db"))
MAC_EPOCH_OFFSET = 978307200


class MessagesDatabaseError(RuntimeError):
    pass


def mac_time_to_datetime(mac_time: int | float | None) -> datetime | None:
    if mac_time is None or mac_time == 0:
        return None
    value = mac_time / 1e9 if mac_time > 1e12 else mac_time
    return datetime.fromtimestamp(value + MAC_EPOCH_OFFSET, tz=timezone.utc)


def decode_attributed_body(blob: bytes | memoryview | None) -> str | None:
    if not blob:
        return None
    data = bytes(blob)
    try:
        plist = plistlib.loads(data)
        objects = plist.get("$objects", [])
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            if "NS.string" in obj:
                return obj["NS.string"]
            if "NSString" in obj:
                uid = obj["NSString"]
                if isinstance(uid, plistlib.UID):
                    idx = uid.data
                    if 0 <= idx < len(objects) and isinstance(objects[idx], str):
                        return objects[idx]
    except Exception:
        pass
    streamtyped = extract_streamtyped_text(data)
    if streamtyped:
        return streamtyped
    return data.decode("utf-8", errors="ignore").strip() or None


def extract_streamtyped_text(data: bytes) -> str | None:
    """Best-effort extraction for Messages streamtyped attributed bodies."""
    marker = b"\x01\x94\x84\x01"
    idx = data.find(marker)
    if idx < 0:
        return None
    pos = idx + len(marker)
    if pos >= len(data):
        return None
    length = data[pos]
    chunk = data[pos + 1 : pos + 1 + length]
    end = chunk.find(b"\x86")
    if end >= 0:
        chunk = chunk[:end]
    text = chunk.decode("utf-8", errors="ignore").strip()
    return text or None


def find_chats(conn: sqlite3.Connection, chat_name: str) -> list[tuple[int, str, str | None, int]]:
    cursor = conn.cursor()
    pattern = f"%{chat_name.lower()}%"
    cursor.execute(
        """
        SELECT c.ROWID, c.chat_identifier, c.display_name, COUNT(cmj.message_id) as msg_count
        FROM chat c
        LEFT JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id
        WHERE LOWER(COALESCE(c.display_name, '')) LIKE ?
           OR LOWER(COALESCE(c.chat_identifier, '')) LIKE ?
        GROUP BY c.ROWID
        ORDER BY msg_count DESC
        """,
        (pattern, pattern),
    )
    return cursor.fetchall()


def list_group_chats(conn: sqlite3.Connection, limit: int = 20) -> list[tuple[int, str | None, str, int]]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT c.ROWID, c.display_name, c.chat_identifier, COUNT(cmj.message_id) as msg_count
        FROM chat c
        LEFT JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id
        WHERE c.style = 43
        GROUP BY c.ROWID
        ORDER BY msg_count DESC
        LIMIT ?
        """,
        (limit,),
    )
    return cursor.fetchall()


def message_text(text: str | None, attributed_body: bytes | memoryview | None) -> str | None:
    if text and text.strip():
        stripped = text.strip()
        cleaned = clean_streamtyped_string(stripped)
        return cleaned or stripped
    decoded = decode_attributed_body(attributed_body)
    if not decoded:
        return None
    stripped = decoded.strip()
    return clean_streamtyped_string(stripped) or stripped


def clean_streamtyped_string(text: str) -> str | None:
    if "streamtyped" not in text[:80] and "\x00" not in text:
        return text
    start = text.find("GeoSports")
    if start >= 0:
        end = text.find("www.geosports.app", start)
        if end >= 0:
            return text[start : end + len("www.geosports.app")].strip()
    link_start = text.find("https://geosports.app/results")
    if link_start >= 0:
        link_end = text.find("\x00", link_start)
        return text[link_start:link_end if link_end >= 0 else None].strip()
    score_message = re.search(r"(GeoSports[\s\S]*?www\.geosports\.app)", text)
    if score_message:
        return score_message.group(1).strip()
    link = re.search(r"https?://geosports\.app/results\?date=\d{4}-\d{2}-\d{2}", text)
    if link:
        return link.group(0)
    return None


def blob_or_text_matches(text: str | None, attributed_body: bytes | memoryview | None) -> bool:
    if text:
        return any(term in text for term in SEARCH_TERMS)
    if attributed_body:
        blob = bytes(attributed_body)
        return any(term.encode("utf-8") in blob for term in SEARCH_TERMS)
    return False


def message_columns(conn: sqlite3.Connection) -> set[str]:
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(message)")
    return {row[1] for row in cursor.fetchall()}


def is_reply_or_reaction(
    associated_type: int | None,
    reply_to_guid: str | None = None,
    thread_originator_guid: str | None = None,
) -> bool:
    return bool(associated_type or reply_to_guid or thread_originator_guid)


def fetch_messages(db_path: Path, chat_ids: list[int]) -> list[RawMessage]:
    if not db_path.exists():
        raise FileNotFoundError(f"Cannot find Messages database at {db_path}")
    if not chat_ids:
        raise ValueError("At least one chat ID is required")

    placeholders = ",".join("?" * len(chat_ids))
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError as exc:
        raise MessagesDatabaseError(
            f"Unable to open {db_path}. Grant Full Disk Access to the terminal app running this command."
        ) from exc
    try:
        cursor = conn.cursor()
        columns = message_columns(conn)
        reply_to_expr = "m.reply_to_guid" if "reply_to_guid" in columns else "NULL"
        thread_originator_expr = "m.thread_originator_guid" if "thread_originator_guid" in columns else "NULL"
        cursor.execute(
            f"""
            SELECT m.date, COALESCE(h.id, 'Me') AS sender, m.text, m.attributedBody,
                   m.associated_message_type, {reply_to_expr}, {thread_originator_expr}
            FROM message m
            JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            WHERE cmj.chat_id IN ({placeholders})
            ORDER BY m.date ASC
            """,
            chat_ids,
        )
        rows: list[RawMessage] = []
        for date, sender, text, attributed_body, associated_type, reply_to_guid, thread_originator_guid in cursor.fetchall():
            if is_reply_or_reaction(associated_type, reply_to_guid, thread_originator_guid):
                continue
            if not blob_or_text_matches(text, attributed_body):
                continue
            timestamp = mac_time_to_datetime(date)
            body = message_text(text, attributed_body)
            if timestamp and body:
                rows.append(RawMessage(timestamp=timestamp, sender=sender, message=body))
        return rows
    finally:
        conn.close()


def resolve_chat_ids(db_path: Path, chat_name: str | None, chat_ids: list[int] | None) -> list[int]:
    if chat_ids:
        return chat_ids
    if not chat_name:
        raise ValueError("Provide --chat-name or --chat-id")
    if not db_path.exists():
        raise FileNotFoundError(f"Cannot find Messages database at {db_path}")

    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.OperationalError as exc:
        raise MessagesDatabaseError(
            f"Unable to open {db_path}. Grant Full Disk Access to the terminal app running this command."
        ) from exc
    try:
        chats = find_chats(conn, chat_name)
        if chats:
            return [row[0] for row in chats]

        groups = list_group_chats(conn)
        preview = "\n".join(
            f"  ID={rowid} | Name={name!r} | Identifier={identifier!r} | {count} messages"
            for rowid, name, identifier, count in groups
        )
        raise ValueError(f"No chats matched {chat_name!r}. Top group chats:\n{preview}")
    finally:
        conn.close()

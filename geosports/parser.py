from __future__ import annotations

import re
from collections.abc import Iterable

from .config import normalize_sender
from .models import RawMessage, ScoreRow

SEARCH_TERMS = ("Geosports", "geosports", "GeoSports", "/1000", "/ 1000", "/1,000", "/ 1,000")
SCORE_RE = re.compile(r"(\d{1,3}(?:,\d{3})?)\s*/\s*1[,.]?000")
EMOJI_RE = re.compile(r"[🟢🟡🔴⚫⬛🔵]{3,}")


def is_geosports_message(message: str) -> bool:
    return any(term in message for term in SEARCH_TERMS)


def parse_score(message: str) -> tuple[int | None, str | None]:
    score_match = SCORE_RE.search(message)
    if not score_match:
        return None, None
    score = int(score_match.group(1).replace(",", ""))
    emoji_match = EMOJI_RE.search(message)
    return score, emoji_match.group(0) if emoji_match else ""


def parse_messages(messages: Iterable[RawMessage]) -> list[ScoreRow]:
    rows: list[ScoreRow] = []
    for raw in messages:
        if not is_geosports_message(raw.message):
            continue
        score, emoji_row = parse_score(raw.message)
        if score is None:
            continue
        rows.append(
            ScoreRow(
                timestamp=raw.timestamp,
                sender=normalize_sender(raw.sender),
                score=score,
                emoji_row=emoji_row or "",
            )
        )
    return rows


def dedupe_scores(rows: Iterable[ScoreRow]) -> list[ScoreRow]:
    """Keep first sender score per day and first instance of each score per day."""
    seen_sender: set[tuple[str, str]] = set()
    seen_score: set[tuple[str, int]] = set()
    deduped: list[ScoreRow] = []

    for row in sorted(rows, key=lambda r: r.timestamp):
        day = row.timestamp.date().isoformat()
        sender_key = (day, row.sender)
        score_key = (day, row.score)

        if sender_key in seen_sender:
            continue
        seen_sender.add(sender_key)

        if score_key in seen_score:
            continue
        seen_score.add(score_key)

        deduped.append(row)

    return deduped

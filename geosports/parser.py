from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date

from .config import normalize_sender
from .models import RawMessage, ScoreRow

# Score-shaped messages from other games must not enter the GeoSports pipeline.
SEARCH_TERMS = ("geosports",)
SCORE_RE = re.compile(r"(\d{1,3}(?:,\d{3})?)\s*/\s*1[,.]?000")
# GeoSports uses a trophy for a 100-point answer; it occupies one question slot.
EMOJI_RE = re.compile(r"[🟢🟡🔴⚫⬛🔵🏆]{3,}")
SAME_SCORE_TIE_START = date(2026, 6, 21)


def is_geosports_message(message: str) -> bool:
    return "geosports" in message.casefold()


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
        if raw.is_reply:
            continue
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
    """Keep first sender score per day; allow same-score ties after cutoff."""
    seen_sender: set[tuple[str, str]] = set()
    seen_score: set[tuple[str, int]] = set()
    deduped: list[ScoreRow] = []

    for row in sorted(rows, key=lambda r: r.timestamp):
        day = row.timestamp.date()
        day_key = day.isoformat()
        sender_key = (day_key, row.sender)
        score_key = (day_key, row.score)

        if sender_key in seen_sender:
            continue
        seen_sender.add(sender_key)

        if day < SAME_SCORE_TIE_START and score_key in seen_score:
            continue
        if day < SAME_SCORE_TIE_START:
            seen_score.add(score_key)

        deduped.append(row)

    return deduped

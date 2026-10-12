from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from .config import normalize_sender
from .models import RawMessage, ScoreRow, is_valid_score

# Score-shaped messages from other games must not enter the GeoSports pipeline.
SEARCH_TERMS = ("geosports",)
SCORE_RE = re.compile(r"(?<![\d,])(\d[\d,]*)\s*/\s*1[,.]?000(?!\d)")
# GeoSports uses a trophy for a 100-point answer; it occupies one question slot.
EMOJI_RE = re.compile(r"[🟢🟡🔴⚫⬛🔵🏆]{3,}")
SAME_SCORE_TIE_START = date(2026, 6, 21)


@dataclass(frozen=True)
class ParseMessagesResult:
    rows: list[ScoreRow]
    rejected_out_of_range: int


def is_geosports_message(message: str) -> bool:
    return "geosports" in message.casefold()


def extract_score(message: str) -> tuple[int | None, str | None]:
    score_match = SCORE_RE.search(message)
    if not score_match:
        return None, None
    score = int(score_match.group(1).replace(",", ""))
    emoji_match = EMOJI_RE.search(message)
    return score, emoji_match.group(0) if emoji_match else ""


def parse_score(message: str) -> tuple[int | None, str | None]:
    """Return a valid GeoSports score, rejecting values outside 1-1000."""
    score, emoji_row = extract_score(message)
    if score is None or not is_valid_score(score):
        return None, None
    return score, emoji_row


def parse_messages_with_diagnostics(messages: Iterable[RawMessage]) -> ParseMessagesResult:
    rows: list[ScoreRow] = []
    rejected_out_of_range = 0
    for raw in messages:
        if raw.is_reply:
            continue
        if not is_geosports_message(raw.message):
            continue
        score, emoji_row = extract_score(raw.message)
        if score is None:
            continue
        if not is_valid_score(score):
            rejected_out_of_range += 1
            continue
        rows.append(
            ScoreRow(
                timestamp=raw.timestamp,
                sender=normalize_sender(raw.sender),
                score=score,
                emoji_row=emoji_row or "",
            )
        )
    return ParseMessagesResult(rows=rows, rejected_out_of_range=rejected_out_of_range)


def parse_messages(messages: Iterable[RawMessage]) -> list[ScoreRow]:
    return parse_messages_with_diagnostics(messages).rows


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

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path

from .config import player_id
from .models import ScoreRow

BACKFILL_HEADERS = {"date", "player_id", "score", "emoji_row"}
QUESTION_MARKERS = {"🟢", "🟡", "🔴", "⚫", "⬛", "🔵"}


class BackfillValidationError(ValueError):
    """Raised when the private backfill reference cannot be safely used."""


@dataclass(frozen=True)
class MergeResult:
    rows: list[ScoreRow]
    messages_accepted: int
    backfill_accepted: int
    collisions_skipped: int
    conflicts: list[str]


def read_backfill_scores(path: Path, player_config: dict) -> list[ScoreRow]:
    """Read private, date-only backfill rows as synthetic score records."""
    if not path.exists():
        return []

    valid_players = set(player_config["players"])
    rows: list[ScoreRow] = []
    seen: set[tuple[str, object]] = set()

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not BACKFILL_HEADERS.issubset(reader.fieldnames):
            required = ", ".join(sorted(BACKFILL_HEADERS))
            raise BackfillValidationError(f"{path.name} must include: {required}")

        for line_number, row in enumerate(reader, start=2):
            try:
                score_date = datetime.strptime(row["date"].strip(), "%Y-%m-%d").date()
            except (KeyError, ValueError) as exc:
                raise BackfillValidationError(f"{path.name}:{line_number} has an invalid date") from exc

            player = row.get("player_id", "").strip()
            if player not in valid_players:
                raise BackfillValidationError(f"{path.name}:{line_number} has an unknown player_id")

            try:
                score = int(row["score"])
            except (KeyError, ValueError) as exc:
                raise BackfillValidationError(f"{path.name}:{line_number} has an invalid score") from exc
            if not 0 <= score <= 1000:
                raise BackfillValidationError(f"{path.name}:{line_number} score must be between 0 and 1000")

            emoji_row = row.get("emoji_row", "").strip()
            if len(emoji_row) != 5 or any(marker not in QUESTION_MARKERS for marker in emoji_row):
                raise BackfillValidationError(f"{path.name}:{line_number} must contain five valid answer markers")

            key = (player, score_date)
            if key in seen:
                raise BackfillValidationError(f"{path.name}:{line_number} duplicates a player/date row")
            seen.add(key)

            # Noon UTC is a stable placeholder; aggregation uses the card's date.
            timestamp = datetime.combine(score_date, time(12), tzinfo=timezone.utc)
            rows.append(ScoreRow(timestamp=timestamp, sender=player, score=score, emoji_row=emoji_row))

    return rows


def merge_score_sources(
    message_rows: list[ScoreRow], backfill_rows: list[ScoreRow], player_config: dict
) -> MergeResult:
    """Keep one player/day score, preferring later-synced Messages data."""
    merged: dict[tuple[str, object], ScoreRow] = {}
    messages_accepted = 0
    backfill_accepted = 0
    collisions_skipped = 0
    conflicts: list[str] = []

    for row in sorted(message_rows, key=lambda item: item.timestamp):
        key = (player_id(row.sender, player_config), row.timestamp.date())
        if key in merged:
            continue
        merged[key] = row
        messages_accepted += 1

    for row in sorted(backfill_rows, key=lambda item: item.timestamp):
        key = (player_id(row.sender, player_config), row.timestamp.date())
        existing = merged.get(key)
        if existing is None:
            merged[key] = row
            backfill_accepted += 1
            continue

        collisions_skipped += 1
        if existing.score != row.score or existing.emoji_row != row.emoji_row:
            conflicts.append(f"{row.timestamp.date().isoformat()} player={key[0]}")

    return MergeResult(
        rows=sorted(merged.values(), key=lambda item: item.timestamp),
        messages_accepted=messages_accepted,
        backfill_accepted=backfill_accepted,
        collisions_skipped=collisions_skipped,
        conflicts=conflicts,
    )

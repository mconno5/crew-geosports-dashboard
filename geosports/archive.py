from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path

from .config import player_id
from .models import ScoreRow

ARCHIVE_HEADERS = ("date", "player_id", "score", "emoji_row")
QUESTION_MARKERS = {"🟢", "🟡", "🔴", "⚫", "⬛", "🔵", "🏆"}
SCHEMA_VERSION = 1


class ArchiveValidationError(ValueError):
    """Raised when the private score archive is not safe to use."""


@dataclass(frozen=True)
class ArchiveSyncResult:
    rows: list[ScoreRow]
    archive_rows: int
    messages_added: int
    backfill_added: int
    matched_rows: int
    conflicts: list[str]

    @property
    def changed(self) -> bool:
        return self.messages_added > 0 or self.backfill_added > 0


def score_key(row: ScoreRow, player_config: dict) -> tuple[str, object]:
    return player_id(row.sender, player_config), row.timestamp.date()


def archive_score_row(score_date, player: str, score: int, emoji_row: str) -> ScoreRow:
    """Create a privacy-minimized score row from a date-only archive record."""
    timestamp = datetime.combine(score_date, time(12), tzinfo=timezone.utc)
    return ScoreRow(timestamp=timestamp, sender=player, score=score, emoji_row=emoji_row)


def valid_emoji_row(emoji_row: str) -> bool:
    """Older valid shares can have no or fewer than five answer markers."""
    return not emoji_row or (
        len(emoji_row) <= 5 and all(marker in QUESTION_MARKERS for marker in emoji_row)
    )


def read_archive_scores(path: Path, player_config: dict) -> list[ScoreRow]:
    """Read the private canonical score history without raw Messages metadata."""
    if not path.exists():
        return []

    valid_players = set(player_config["players"])
    rows: list[ScoreRow] = []
    seen: set[tuple[str, object]] = set()

    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or not set(ARCHIVE_HEADERS).issubset(reader.fieldnames):
            required = ", ".join(ARCHIVE_HEADERS)
            raise ArchiveValidationError(f"{path.name} must include: {required}")

        for line_number, row in enumerate(reader, start=2):
            try:
                score_date = datetime.strptime(row["date"].strip(), "%Y-%m-%d").date()
            except (KeyError, ValueError) as exc:
                raise ArchiveValidationError(f"{path.name}:{line_number} has an invalid date") from exc

            player = row.get("player_id", "").strip()
            if player not in valid_players:
                raise ArchiveValidationError(f"{path.name}:{line_number} has an unknown player_id")

            try:
                score = int(row["score"])
            except (KeyError, ValueError) as exc:
                raise ArchiveValidationError(f"{path.name}:{line_number} has an invalid score") from exc
            if not 0 <= score <= 1000:
                raise ArchiveValidationError(f"{path.name}:{line_number} score must be between 0 and 1000")

            emoji_row = row.get("emoji_row", "").strip()
            if not valid_emoji_row(emoji_row):
                raise ArchiveValidationError(
                    f"{path.name}:{line_number} must be blank or contain up to five valid answer markers"
                )

            key = (player, score_date)
            if key in seen:
                raise ArchiveValidationError(f"{path.name}:{line_number} duplicates a player/date row")
            seen.add(key)
            rows.append(archive_score_row(score_date, player, score, emoji_row))

    return sorted(rows, key=lambda row: (row.timestamp, row.sender))


def sync_archive(
    archive_rows: list[ScoreRow],
    message_rows: list[ScoreRow],
    backfill_rows: list[ScoreRow],
    player_config: dict,
) -> ArchiveSyncResult:
    """Append new scores while preserving existing archive records on conflicts."""
    merged: dict[tuple[str, object], ScoreRow] = {}
    for row in sorted(archive_rows, key=lambda item: (item.timestamp, item.sender)):
        key = score_key(row, player_config)
        if key in merged:
            raise ArchiveValidationError("archive rows must be unique by player/date")
        merged[key] = archive_score_row(key[1], key[0], row.score, row.emoji_row)

    messages_added = 0
    backfill_added = 0
    matched_rows = 0
    conflicts: list[str] = []

    for source, source_rows in (("messages", message_rows), ("backfill", backfill_rows)):
        for row in sorted(source_rows, key=lambda item: item.timestamp):
            player, score_date = score_key(row, player_config)
            key = (player, score_date)
            existing = merged.get(key)
            if existing is None:
                merged[key] = archive_score_row(score_date, player, row.score, row.emoji_row)
                if source == "messages":
                    messages_added += 1
                else:
                    backfill_added += 1
                continue
            if existing.score == row.score and existing.emoji_row == row.emoji_row:
                matched_rows += 1
            else:
                conflicts.append(f"{score_date.isoformat()} player={player} source={source}")

    return ArchiveSyncResult(
        rows=sorted(merged.values(), key=lambda item: (item.timestamp, item.sender)),
        archive_rows=len(archive_rows),
        messages_added=messages_added,
        backfill_added=backfill_added,
        matched_rows=matched_rows,
        conflicts=conflicts,
    )


def archive_csv_text(rows: list[ScoreRow], player_config: dict) -> str:
    """Serialize only public player IDs and score facts for the private archive."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(ARCHIVE_HEADERS)
    serialized = []
    for row in rows:
        player, score_date = score_key(row, player_config)
        serialized.append((score_date, player, row.score, row.emoji_row))
    for score_date, player, score, emoji_row in sorted(serialized):
        writer.writerow([score_date.isoformat(), player, score, emoji_row])
    return output.getvalue()


def atomic_write_text(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as file:
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def build_manifest(rows: list[ScoreRow], player_config: dict) -> dict:
    csv_text = archive_csv_text(rows, player_config)
    dates = [row.timestamp.date() for row in rows]
    counts: dict[str, int] = {}
    for row in rows:
        player, _ = score_key(row, player_config)
        counts[player] = counts.get(player, 0) + 1
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "scoreCount": len(rows),
        "dateRange": {
            "start": min(dates).isoformat() if dates else None,
            "end": max(dates).isoformat() if dates else None,
        },
        "playerCounts": dict(sorted(counts.items())),
        "sha256": hashlib.sha256(csv_text.encode("utf-8")).hexdigest(),
    }


def manifest_status(path: Path, rows: list[ScoreRow], player_config: dict) -> str:
    """Return a concise integrity state without exposing any archived row data."""
    if not path.exists():
        return "missing"
    try:
        saved_manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unreadable"
    current_manifest = build_manifest(rows, player_config)
    return "current" if saved_manifest.get("sha256") == current_manifest["sha256"] else "mismatch"


def write_archive(path: Path, manifest_path: Path, rows: list[ScoreRow], player_config: dict) -> None:
    """Atomically update the archive and its privacy-safe integrity manifest."""
    csv_text = archive_csv_text(rows, player_config)
    manifest = build_manifest(rows, player_config)
    atomic_write_text(path, csv_text)
    atomic_write_text(manifest_path, json.dumps(manifest, indent=2) + "\n")


def reconcile_archive_row(
    archive_rows: list[ScoreRow],
    player: str,
    score_date,
    score: int,
    emoji_row: str,
    player_config: dict,
) -> list[ScoreRow]:
    """Replace one archived score only after an explicit operator decision."""
    if player not in player_config["players"]:
        raise ArchiveValidationError("unknown player_id")
    if not 0 <= score <= 1000:
        raise ArchiveValidationError("score must be between 0 and 1000")
    if not valid_emoji_row(emoji_row):
        raise ArchiveValidationError("emoji_row must be blank or contain up to five valid answer markers")

    key = (player, score_date)
    replaced = False
    rows: list[ScoreRow] = []
    for row in archive_rows:
        if score_key(row, player_config) == key:
            rows.append(archive_score_row(score_date, player, score, emoji_row))
            replaced = True
        else:
            rows.append(row)
    if not replaced:
        rows.append(archive_score_row(score_date, player, score, emoji_row))
    return sorted(rows, key=lambda row: (row.timestamp, row.sender))

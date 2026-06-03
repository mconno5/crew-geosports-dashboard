from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from .models import RawMessage, ScoreRow

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_timestamp(value: datetime) -> str:
    return value.strftime(DATETIME_FORMAT)


def parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, DATETIME_FORMAT)


def write_raw_csv(path: Path, rows: list[RawMessage]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "sender", "message"])
        for row in rows:
            writer.writerow([format_timestamp(row.timestamp), row.sender, row.message])


def write_scores_csv(path: Path, rows: list[ScoreRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "sender", "score", "emoji_row"])
        for row in rows:
            writer.writerow([format_timestamp(row.timestamp), row.sender, row.score, row.emoji_row])


def read_scores_csv(path: Path) -> list[ScoreRow]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [
            ScoreRow(
                timestamp=parse_timestamp(row["timestamp"]),
                sender=row["sender"],
                score=int(row["score"]),
                emoji_row=row.get("emoji_row", ""),
            )
            for row in reader
        ]

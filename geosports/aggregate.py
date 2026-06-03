from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean

from .config import normalize_sender, player_display
from .models import ScoreRow


def ordinal_day(dt) -> str:
    suffix = "th" if 11 <= dt.day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(dt.day % 10, "th")
    return dt.strftime("%b ") + f"{dt.day}{suffix}"


def display_range(start, end) -> str:
    if start.year == end.year:
        if start.month == end.month:
            return f"{start.strftime('%B')} {start.day} - {end.day}, {end.year}"
        return f"{start.strftime('%B')} {start.day} - {end.strftime('%B')} {end.day}, {end.year}"
    return f"{start.strftime('%B')} {start.day}, {start.year} - {end.strftime('%B')} {end.day}, {end.year}"


def build_dashboard_data(rows: list[ScoreRow], player_config: dict[str, dict[str, str]]) -> dict:
    if not rows:
        generated = datetime.now(timezone.utc).date().isoformat()
        return {
            "meta": {
                "dateRange": "No scores yet",
                "generatedLabel": generated,
                "playerCount": 0,
                "scoreCount": 0,
                "groupAverage": 0,
                "highScore": None,
                "lowScore": None,
            },
            "players": [],
            "dates": [],
            "dailyScores": {},
        }

    sorted_rows = sorted(rows, key=lambda r: r.timestamp)
    by_sender: dict[str, list[ScoreRow]] = defaultdict(list)
    for row in sorted_rows:
        by_sender[normalize_sender(row.sender)].append(row)

    player_rows = []
    for index, (sender, sender_rows) in enumerate(by_sender.items()):
        display = player_display(sender, player_config, index)
        scores = [r.score for r in sender_rows]
        player_rows.append(
            {
                "id": display["id"],
                "name": display["name"],
                "avg": round(mean(scores)),
                "count": len(scores),
                "best": max(scores),
                "worst": min(scores),
                "color": display["color"],
            }
        )
    player_rows.sort(key=lambda p: (-p["avg"], -p["count"], p["name"]))

    start_date = sorted_rows[0].timestamp.date()
    end_date = sorted_rows[-1].timestamp.date()
    all_dates = []
    current = start_date
    while current <= end_date:
        all_dates.append(current)
        current = current.fromordinal(current.toordinal() + 1)

    by_sender_day = {(normalize_sender(r.sender), r.timestamp.date()): r.score for r in sorted_rows}
    daily_scores = {
        player["id"]: [by_sender_day.get((player["id"], day)) for day in all_dates]
        for player in player_rows
    }

    high = max(sorted_rows, key=lambda r: r.score)
    low = min(sorted_rows, key=lambda r: r.score)
    names_by_id = {p["id"]: p["name"] for p in player_rows}

    return {
        "meta": {
            "dateRange": display_range(start_date, end_date),
            "generatedLabel": datetime.now(timezone.utc).strftime("%B %-d, %Y"),
            "playerCount": len(player_rows),
            "scoreCount": len(sorted_rows),
            "groupAverage": round(mean(r.score for r in sorted_rows)),
            "highScore": {
                "score": high.score,
                "player": names_by_id.get(normalize_sender(high.sender), normalize_sender(high.sender)),
                "date": ordinal_day(high.timestamp),
            },
            "lowScore": {
                "score": low.score,
                "player": names_by_id.get(normalize_sender(low.sender), normalize_sender(low.sender)),
                "date": ordinal_day(low.timestamp),
            },
        },
        "players": player_rows,
        "dates": [day.strftime("%m-%d") for day in all_dates],
        "dailyScores": daily_scores,
    }

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean

from .config import player_display, player_id
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


def build_dashboard_data(rows: list[ScoreRow], player_config: dict) -> dict:
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

    # Multiple raw senders (e.g. "Me" and a phone number) can resolve to the
    # same player, so the one-score-per-player-per-day rule is re-applied
    # here at the player level: first chronological score wins.
    by_player: dict[str, list[ScoreRow]] = defaultdict(list)
    score_by_player_day: dict[tuple, int] = {}
    kept_rows: list[tuple[str, ScoreRow]] = []
    for row in sorted_rows:
        pid = player_id(row.sender, player_config)
        key = (pid, row.timestamp.date())
        if key in score_by_player_day:
            continue
        score_by_player_day[key] = row.score
        by_player[pid].append(row)
        kept_rows.append((pid, row))

    player_rows = []
    for index, (pid, p_rows) in enumerate(by_player.items()):
        display = player_display(pid, player_config, index)
        scores = [r.score for r in p_rows]
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

    daily_scores = {
        player["id"]: [score_by_player_day.get((player["id"], day)) for day in all_dates]
        for player in player_rows
    }

    high_pid, high = max(kept_rows, key=lambda item: item[1].score)
    low_pid, low = min(kept_rows, key=lambda item: item[1].score)
    names_by_id = {p["id"]: p["name"] for p in player_rows}

    return {
        "meta": {
            "dateRange": display_range(start_date, end_date),
            "generatedLabel": datetime.now(timezone.utc).strftime("%B %-d, %Y"),
            "playerCount": len(player_rows),
            "scoreCount": len(kept_rows),
            "groupAverage": round(mean(item[1].score for item in kept_rows)),
            "highScore": {
                "score": high.score,
                "player": names_by_id.get(high_pid, high_pid),
                "date": ordinal_day(high.timestamp),
            },
            "lowScore": {
                "score": low.score,
                "player": names_by_id.get(low_pid, low_pid),
                "date": ordinal_day(low.timestamp),
            },
        },
        "players": player_rows,
        "dates": [day.strftime("%m-%d") for day in all_dates],
        "dailyScores": daily_scores,
    }

from __future__ import annotations

import json
import re
from pathlib import Path


def js_const(name: str, value) -> str:
    return f"const {name} = {json.dumps(value, ensure_ascii=False, indent=2)};"


def render_dashboard(template_path: Path, output_path: Path, dashboard_data: dict) -> None:
    html = template_path.read_text(encoding="utf-8")
    meta = dashboard_data["meta"]
    subtitle = (
        f"{meta['dateRange']} &nbsp;·&nbsp; "
        f"{meta['playerCount']} players &nbsp;·&nbsp; "
        f"{meta['scoreCount']} scores logged"
    )

    replacements = {
        r'<div class="header-sub">.*?</div>': f'<div class="header-sub">{subtitle}</div>',
        r"<span>Generated .*?</span>": f"<span>Generated {meta['generatedLabel']}</span>",
        r'<div class="stat-label">Group avg</div>\s*<div class="stat-val">.*?</div>': (
            f'<div class="stat-label">Group avg</div>\n'
            f'        <div class="stat-val">{meta["groupAverage"]}</div>'
        ),
        r'<div class="stat-label">Games played</div>\s*<div class="stat-val">.*?</div>\s*<div class="stat-sub">.*?</div>': (
            f'<div class="stat-label">Games played</div>\n'
            f'        <div class="stat-val">{meta["scoreCount"]}</div>\n'
            f'        <div class="stat-sub">across {meta["playerCount"]} players</div>'
        ),
    }
    for pattern, replacement in replacements.items():
        html = re.sub(pattern, replacement, html, count=1, flags=re.DOTALL)

    high = meta.get("highScore")
    low = meta.get("lowScore")
    if high:
        html = re.sub(
            r'<div class="stat-label">All-time high</div>\s*<div class="stat-val" style="color:#4ae8a0">.*?</div>\s*<div class="stat-sub">.*?</div>',
            f'<div class="stat-label">All-time high</div>\n'
            f'        <div class="stat-val" style="color:#4ae8a0">{high["score"]}</div>\n'
            f'        <div class="stat-sub">{high["player"]} · {high["date"]}</div>',
            html,
            count=1,
            flags=re.DOTALL,
        )
    if low:
        html = re.sub(
            r'<div class="stat-label">All-time low</div>\s*<div class="stat-val" style="color:#e84a4a">.*?</div>\s*<div class="stat-sub">.*?</div>',
            f'<div class="stat-label">All-time low</div>\n'
            f'        <div class="stat-val" style="color:#e84a4a">{low["score"]}</div>\n'
            f'        <div class="stat-sub">{low["player"]} · {low["date"]}</div>',
            html,
            count=1,
            flags=re.DOTALL,
        )

    html = re.sub(r"const players = \[.*?\];", js_const("players", dashboard_data["players"]), html, count=1, flags=re.DOTALL)
    html = re.sub(r"const dates = \[.*?\];", js_const("dates", dashboard_data["dates"]), html, count=1, flags=re.DOTALL)
    html = re.sub(
        r"const dailyScores = \{.*?\};\n\nconst maxAvg",
        js_const("dailyScores", dashboard_data["dailyScores"]) + "\n\nconst maxAvg",
        html,
        count=1,
        flags=re.DOTALL,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")

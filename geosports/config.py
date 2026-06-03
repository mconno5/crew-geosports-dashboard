from __future__ import annotations

import json
import re
from pathlib import Path


DEFAULT_COLORS = [
    "#4ae8e8",
    "#4ae8a0",
    "#e8734a",
    "#e84a8a",
    "#4a9ee8",
    "#c4a84a",
    "#7ae84a",
    "#a04ae8",
    "#e8c84a",
    "#b8a0ff",
]


def normalize_sender(sender: str) -> str:
    """Normalize phone-style sender IDs while preserving emails and 'Me'."""
    if sender == "Me":
        return sender
    digits = re.sub(r"\D", "", sender)
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits or sender.strip()


def load_player_config(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    return {normalize_sender(k): v for k, v in raw.items()}


def player_display(sender: str, players: dict[str, dict[str, str]], index: int) -> dict[str, str]:
    normalized = normalize_sender(sender)
    configured = players.get(normalized, {})
    return {
        "id": normalized,
        "name": configured.get("name", normalized),
        "color": configured.get("color", DEFAULT_COLORS[index % len(DEFAULT_COLORS)]),
    }

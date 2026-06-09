from __future__ import annotations

import hashlib
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

SENDERS_FILENAME = "senders.local.json"


def normalize_sender(sender: str) -> str:
    """Normalize phone-style sender IDs while preserving emails and 'Me'."""
    if sender == "Me":
        return sender
    digits = re.sub(r"\D", "", sender)
    if len(digits) == 11 and digits.startswith("1"):
        return digits[1:]
    return digits or sender.strip()


def load_player_config(path: Path) -> dict:
    """Load the public player config plus the private sender mapping.

    `players.json` (committed) is keyed by non-sensitive slug and holds display
    name and color. `senders.local.json` (gitignored, same directory) maps raw
    sender IDs such as phone numbers or 'Me' to those slugs.
    """
    players: dict[str, dict[str, str]] = {}
    if path.exists():
        with path.open(encoding="utf-8") as f:
            players = json.load(f)

    senders: dict[str, str] = {}
    senders_path = path.with_name(SENDERS_FILENAME)
    if senders_path.exists():
        with senders_path.open(encoding="utf-8") as f:
            senders = {normalize_sender(k): v for k, v in json.load(f).items()}

    return {"players": players, "senders": senders}


def player_id(sender: str, config: dict) -> str:
    """Resolve a raw sender to its public player ID.

    Unmapped senders get a stable hashed ID so phone numbers never reach
    generated output.
    """
    normalized = normalize_sender(sender)
    slug = config["senders"].get(normalized)
    if slug:
        return slug
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
    return f"player-{digest}"


def player_display(pid: str, config: dict, index: int) -> dict[str, str]:
    configured = config["players"].get(pid, {})
    fallback_name = f"Player {pid[-4:]}" if pid.startswith("player-") else pid
    return {
        "id": pid,
        "name": configured.get("name", fallback_name),
        "color": configured.get("color", DEFAULT_COLORS[index % len(DEFAULT_COLORS)]),
    }

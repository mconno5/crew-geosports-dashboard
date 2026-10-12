from dataclasses import dataclass
from datetime import datetime

MIN_SCORE = 1
MAX_SCORE = 1000


def is_valid_score(score: int) -> bool:
    """GeoSports scores are positive and cannot exceed the game's maximum."""
    return MIN_SCORE <= score <= MAX_SCORE


@dataclass(frozen=True)
class RawMessage:
    timestamp: datetime
    sender: str
    message: str
    is_reply: bool = False


@dataclass(frozen=True)
class ScoreRow:
    timestamp: datetime
    sender: str
    score: int
    emoji_row: str = ""

from dataclasses import dataclass
from datetime import datetime


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

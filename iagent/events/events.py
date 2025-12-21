from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


@dataclass
class Field:
    description: str


class Event(Field, Enum):
    POSSIBLE_LOCK_UP = Field(
        description="Driver likely locked up."
    )
    LAP_START = Field(
        description="Driver started a new lap."
    )
    LAP_END = Field(
        description="Driver ended a lap."
    )
    ENTERED_STRAIGHT = Field(
        description="Driver entered a straight section of the track."
    )


class EventPriority(int, Enum):
    # Lower number for a higher priority
    HIGH = 0
    LOW = 1


@dataclass
class EventStamp:
    lap_dist: float
    session_tick: int
    event: Event
    priority: EventPriority
    associated_data: dict = field(default_factory=lambda: {})
    date: datetime = field(default_factory=lambda: datetime.now())

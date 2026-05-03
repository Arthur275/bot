from __future__ import annotations

from enum import Enum


class PositionAction(str, Enum):
    ENTRY_LONG = "entry_long"
    ENTRY_SHORT = "entry_short"
    SMALL_PROBE = "small_probe"
    REDUCE = "reduce"
    EXIT = "exit"
    WAIT = "wait"
    OBSERVE_ONLY = "observe_only"
    PAPER_ONLY = "paper_only"

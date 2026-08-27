from typing import Literal

Decision = Literal["ACCORD", "REFUS", "INSTRUCTION_MANUELLE"]
DECISIONS: tuple[Decision, ...] = (
    "ACCORD",
    "REFUS",
    "INSTRUCTION_MANUELLE",
)


def parse_decision(value: str) -> Decision:
    if value not in DECISIONS:
        raise ValueError(f"unknown decision: {value}")
    return value

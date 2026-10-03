from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class AgentUiError(Exception):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def agent_ui_error(code: str, status: int, message: str | None = None) -> AgentUiError:
    return AgentUiError(code, message or code, status)


@dataclass
class ConfirmationChallenge:
    confirmationId: str
    action: str
    target: str
    impact: str
    reversible: bool
    token: str

    def to_json(self) -> dict[str, Any]:
        return {
            "confirmationId": self.confirmationId,
            "action": self.action,
            "target": self.target,
            "impact": self.impact,
            "reversible": self.reversible,
            "token": self.token,
        }


class ConfirmationRequiredError(AgentUiError):
    def __init__(self, confirmation: ConfirmationChallenge) -> None:
        super().__init__("confirmation_required", "confirmation_required", 409)
        self.confirmation = confirmation

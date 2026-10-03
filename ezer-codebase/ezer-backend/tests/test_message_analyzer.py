"""Pipeline d'analyse : règles locales par défaut, Jev (client injecté) quand il est branché."""

from __future__ import annotations

from typing import Any

import pytest

from app.analysis.analyzer import MessageAnalyzer
from app.domain.models import MailMessage

MESSAGE = MailMessage(
    account_id="a",
    provider="gmail",
    provider_message_id="m1",
    subject="Validation du budget",
    sender_name="Claire",
    sender_address="claire@example.com",
    received_at="2026-08-27T09:00:00.000Z",
    body_text="Merci de valider le budget avant le 30 août 2026.",
    snippet="Validation attendue.",
)


def answers(**overrides: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "category": {"type": "choice", "choice": "action_required", "confidence": 0.9},
        "prompt_injection": {"type": "noul", "noul": 0.01},
        "phishing": {"type": "noul", "noul": 0.02},
        "urgent": {"type": "noul", "noul": 0.1},
        "action_required": {"type": "noul", "noul": 0.95},
    }
    for key, value in overrides.items():
        merged[key] = {"type": "noul", "noul": value} if isinstance(value, int | float) else {"type": "choice", **value}
    return {"answers": merged}


class FakeJev:
    """Double de `TypeSafeClient.systemOne` : une réponse de jugements, une de routage."""

    def __init__(self, route: dict[str, Any] | None = None, judgments: dict[str, Any] | None = None) -> None:
        self.route = route or {"choice": "surface", "confidence": 0.9}
        self.judgments = judgments or answers()
        self.calls: list[dict[str, Any]] = []
        self.failure: Exception | None = None
        self.route_failure: Exception | None = None

    async def system_one(self, *, model: str, state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"model": model, "state": state, "questions": questions})
        if "route" in questions:
            if self.route_failure is not None:
                raise self.route_failure
            return {"answers": {"route": {"type": "choice", **self.route}}}
        if self.failure is not None:
            raise self.failure
        return self.judgments


async def test_uses_local_rules_when_no_typesafe_key_is_configured():
    analysis = await MessageAnalyzer().analyse(MESSAGE)
    assert analysis.model_id == "ezer-typescript-rules-v1"
    assert analysis.pipeline_version == "ezer-ts-v1"
    assert analysis.prompt_version == "heuristic-fr-v1"


async def test_a_typesafe_key_without_an_adapter_keeps_the_local_rules(caplog):
    with caplog.at_level("WARNING", logger="ezer.analysis"):
        analyzer = MessageAnalyzer(typesafe_api_key="test-key")
    assert "local rules" in caplog.text
    assert (await analyzer.analyse(MESSAGE)).model_id == "ezer-typescript-rules-v1"


async def test_derives_triage_from_jev_judgments_and_records_the_provenance():
    jev = FakeJev()
    analysis = await MessageAnalyzer(jev).analyse(MESSAGE)
    assert analysis.model_id == "jev-latest"
    assert analysis.pipeline_version == "ezer-jev-v1"
    assert analysis.prompt_version == "jev-mail-questions-v1"
    assert analysis.category == "action_required"
    assert analysis.priority == "high"
    assert analysis.needs_human_review is False
    assert analysis.triage.confidence == 0.9
    assert analysis.action_items[0].due_date == "30 août 2026"


async def test_only_the_documented_fields_are_sent_to_jev():
    jev = FakeJev()
    await MessageAnalyzer(jev).analyse(MESSAGE)
    first = jev.calls[0]
    assert first["model"] == "jev-latest"
    assert set(first["state"]) == {"subject", "sender", "snippet", "body"}
    assert first["state"]["sender"] == "Claire <claire@example.com>"
    assert set(first["questions"]) == {"category", "prompt_injection", "phishing", "urgent", "action_required"}


async def test_escalates_phishing_to_a_human_regardless_of_the_category_choice():
    jev = FakeJev(
        {"choice": "auto_file", "confidence": 0.95},
        answers(phishing=0.97, urgent=0.9, category={"choice": "informational", "confidence": 0.8}),
    )
    analysis = await MessageAnalyzer(jev).analyse(MESSAGE)
    assert analysis.category == "security"
    assert analysis.priority == "critical"
    assert analysis.needs_human_review is True
    assert analysis.safety.phishing_likelihood == pytest.approx(0.97)


async def test_sends_an_uncertain_category_to_human_review():
    jev = FakeJev(
        {"choice": "surface", "confidence": 0.9},
        answers(category={"choice": "other", "confidence": 0.2}, action_required=0.1),
    )
    assert (await MessageAnalyzer(jev).analyse(MESSAGE)).needs_human_review is True


async def test_falls_back_to_local_rules_through_the_pipeline_when_the_service_fails():
    jev = FakeJev()
    jev.failure = RuntimeError("down")
    analysis = await MessageAnalyzer(jev).analyse(MESSAGE)
    assert len(jev.calls) == 1
    assert analysis.model_id == "ezer-typescript-rules-v1"


async def test_lets_the_jev_route_decide_auto_file_lowers_priority_human_review_escalates():
    jev = FakeJev(
        {"choice": "auto_file", "confidence": 0.9},
        answers(action_required=0.05, category={"choice": "newsletter", "confidence": 0.9}),
    )
    filed = await MessageAnalyzer(jev).analyse(MESSAGE)
    assert filed.priority == "low"
    assert filed.needs_human_review is False
    assert len(jev.calls) == 2

    jev = FakeJev({"choice": "human_review", "confidence": 0.9})
    escalated = await MessageAnalyzer(jev).analyse(MESSAGE)
    assert escalated.needs_human_review is True


async def test_escalates_when_the_route_decision_has_low_confidence():
    jev = FakeJev({"choice": "auto_file", "confidence": 0.1})
    assert (await MessageAnalyzer(jev).analyse(MESSAGE)).needs_human_review is True


async def test_falls_back_to_rules_when_only_the_route_decision_fails():
    jev = FakeJev()
    jev.route_failure = RuntimeError("down")
    assert (await MessageAnalyzer(jev).analyse(MESSAGE)).model_id == "ezer-typescript-rules-v1"

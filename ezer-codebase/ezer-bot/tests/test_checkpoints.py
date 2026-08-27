from __future__ import annotations

from datetime import UTC, datetime

from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from ezer.checkpoints import _CHECKPOINT_MODEL_ALLOWLIST
from ezer.domain import EmailAnalysis, SafetyAssessment


def test_strict_checkpoint_serializer_round_trips_domain_models() -> None:
    analysis = EmailAnalysis(
        analysis_id="a" * 64,
        message_ref="gmail:primary:message-1",
        content_hash="b" * 64,
        pipeline_version="pipeline-v1",
        model_id="claude-sonnet-5",
        prompt_version="prompt-v1",
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
        category="informational",
        priority="normal",
        needs_human_review=False,
        summary="Résumé sûr.",
        safety=SafetyAssessment(
            risk_level="none",
            phishing_likelihood=0,
            rationale="Aucun indicateur.",
            confidence=1,
        ),
    )
    base = JsonPlusSerializer(allowed_msgpack_modules=_CHECKPOINT_MODEL_ALLOWLIST)
    serializer = EncryptedSerializer.from_pycryptodome_aes(
        serde=base,
        key=b"0123456789abcdef0123456789abcdef",
    )

    encoded = serializer.dumps_typed({"analysis": analysis})
    decoded = serializer.loads_typed(encoded)

    assert encoded[0] == "msgpack+aes"
    assert isinstance(decoded["analysis"], EmailAnalysis)
    assert isinstance(decoded["analysis"].safety, SafetyAssessment)
    assert decoded["analysis"] == analysis

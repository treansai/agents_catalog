from __future__ import annotations

from app.analysis.assemble import assemble_analysis
from app.analysis.signals import MODEL_ID, PIPELINE_VERSION, PROMPT_VERSION, RULES_PROVENANCE, rule_signals
from app.domain.models import EmailAnalysis, MailMessage
from app.services.timeutil import now_iso

__all__ = ["MODEL_ID", "PIPELINE_VERSION", "PROMPT_VERSION", "analyse_message"]


def analyse_message(message: MailMessage, created_at: str | None = None) -> EmailAnalysis:
    """Analyse déterministe par règles locales : graine de démonstration et repli sans Jev."""
    return assemble_analysis(message, created_at or now_iso(), rule_signals(message), RULES_PROVENANCE)

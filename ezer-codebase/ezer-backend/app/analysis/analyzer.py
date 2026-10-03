from __future__ import annotations

import logging

from app.analysis.jev import JevClient
from app.analysis.pipeline import run_analysis
from app.domain.models import EmailAnalysis, MailMessage
from app.services.timeutil import now_iso

logger = logging.getLogger("ezer.analysis")


class MessageAnalyzer:
    """Façade du pipeline d'analyse : Jev si un client est fourni, sinon règles locales (ezer-ts-v1)."""

    def __init__(self, client: JevClient | None = None, typesafe_api_key: str | None = None) -> None:
        self._client = client
        if client is None and typesafe_api_key:
            logger.warning(
                "TYPESAFE_API_KEY is set but no Jev adapter is configured in the Python port; "
                "local rules (ezer-ts-v1) apply"
            )

    async def analyse(self, message: MailMessage, created_at: str | None = None) -> EmailAnalysis:
        state = await run_analysis(self._client, message, created_at or now_iso())
        if state.jev_error is not None:
            logger.warning("Jev unavailable, using local rules (%s)", state.jev_error)
        if state.analysis is None:
            raise RuntimeError("analysis pipeline produced no result")
        return state.analysis

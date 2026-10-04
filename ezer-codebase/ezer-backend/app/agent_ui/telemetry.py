from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("ezer.agent_ui")

SENSITIVE_KEYS = frozenset(
    {"email", "token", "secret", "password", "body", "snippet", "subject", "address", "props", "data"}
)


def emit_agent_ui_event(**entry: Any) -> None:
    """Journalise un événement d'interface agent, champs sensibles masqués.

    Les clés gardent le nom camelCase du journal d'origine (event, traceId, durationMs, ...).
    """
    redacted: dict[str, Any] = {}
    for key, value in entry.items():
        if value is None:
            continue
        redacted[key] = "[redacted]" if key.lower() in SENSITIVE_KEYS else value
    logger.info(json.dumps(redacted, ensure_ascii=False))

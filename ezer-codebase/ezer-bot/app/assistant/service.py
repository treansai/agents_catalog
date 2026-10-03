"""Lazy construction of the assistant once the settings are complete."""

import httpx

from app.agent_ui.agent_ui import HttpAgentUiClient
from app.assistant.assistant import MailboxAssistant
from app.assistant.chat_model import ChatModelFactory, create_anthropic_chat_model
from app.config import Settings
from app.mailbox.mailbox import HttpMailboxClient


class AssistantService:
    def __init__(
        self,
        settings: Settings,
        *,
        model_factory: ChatModelFactory = create_anthropic_chat_model,
        transport: httpx.AsyncBaseTransport | None = None,
        assistant: MailboxAssistant | None = None,
    ) -> None:
        self._settings = settings
        self._model_factory = model_factory
        self._transport = transport
        self._assistant = assistant

    @property
    def configured(self) -> bool:
        return self._assistant is not None or self._settings.assistant_configured

    def instance(self) -> MailboxAssistant:
        if self._assistant is None:
            url = self._settings.backend_url
            key = self._settings.backend_api_key
            if url is None or key is None:
                raise RuntimeError("assistant backend is not configured")
            self._assistant = MailboxAssistant(
                self._settings,
                HttpMailboxClient(url, key, transport=self._transport),
                self._model_factory,
                HttpAgentUiClient(url, key, transport=self._transport),
            )
        return self._assistant

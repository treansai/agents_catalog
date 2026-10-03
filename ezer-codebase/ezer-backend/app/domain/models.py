"""Modèles du domaine. Les noms de champs sont ceux, en snake_case, du contrat HTTP."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

PROVIDERS = ("gmail", "outlook")
Provider = Literal["gmail", "outlook"]

EMAIL_CATEGORIES = (
    "action_required",
    "informational",
    "newsletter",
    "receipt",
    "security",
    "spam",
    "other",
)
EmailCategory = Literal["action_required", "informational", "newsletter", "receipt", "security", "spam", "other"]

PRIORITIES = ("low", "normal", "high", "critical")
Priority = Literal["low", "normal", "high", "critical"]

RISK_LEVELS = ("none", "low", "medium", "high")
RiskLevel = Literal["none", "low", "medium", "high"]

CONNECTION_STATUSES = ("disconnected", "pending", "connected", "failed")
ConnectionStatus = Literal["disconnected", "pending", "connected", "failed"]


class Account(BaseModel):
    id: str
    provider: Provider
    # Adresse de la boîte, requise pour connecter un compte Outlook réel.
    mailbox: str | None = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "provider": self.provider}
        if self.mailbox is not None:
            data["mailbox"] = self.mailbox
        return data


class AccountSummary(BaseModel):
    """Vue publique d'un compte : identité, boîte et état de connexion, jamais de jeton."""

    id: str
    provider: Provider
    mailbox: str | None
    status: ConnectionStatus
    connected_at: str | None
    write_enabled: bool


class ConnectionState(BaseModel):
    account_id: str
    provider: Provider
    mailbox: str | None
    status: ConnectionStatus
    # Code d'erreur stable, jamais un message du fournisseur.
    code: str | None
    verification_uri: str | None
    user_code: str | None
    expires_at: str | None
    connected_at: str | None
    write_enabled: bool


class MailMessage(BaseModel):
    account_id: str
    provider: Provider
    provider_message_id: str
    thread_id: str | None = None
    subject: str
    sender_name: str
    sender_address: str
    received_at: str
    body_text: str
    snippet: str


class ActionItem(BaseModel):
    description: str
    owner: str | None
    due_date: str | None
    confidence: float


class SafetyAssessment(BaseModel):
    risk_level: RiskLevel
    prompt_injection_detected: bool
    phishing_likelihood: float
    indicators: list[str]
    rationale: str
    confidence: float


class TriageResult(BaseModel):
    category: EmailCategory
    priority: Priority
    needs_human_review: bool
    confidence: float
    rationale: str


class EmailAnalysis(BaseModel):
    model_config = ConfigDict(extra="allow")

    analysis_id: str
    message_ref: str
    content_hash: str
    pipeline_version: str
    model_id: str
    prompt_version: str
    created_at: str
    category: EmailCategory
    priority: Priority
    needs_human_review: bool
    summary: str
    key_points: list[str]
    action_items: list[ActionItem]
    safety: SafetyAssessment
    triage: TriageResult
    detected_language: str


class SyncReport(BaseModel):
    account_id: str
    provider: Provider
    fetched: int
    processed: int
    skipped: int
    failed: int
    dead_lettered: int
    cursor_advanced: bool
    cursor_reset: bool
    analyses: list[dict[str, Any]]


class FetchBatch(BaseModel):
    messages: list[MailMessage]
    next_cursor: str | None
    cursor_reset: bool


class AnalysisFilters(BaseModel):
    account_id: str | None = None
    category: str | None = None
    priority: str | None = None
    needs_human_review: bool | None = None

"""Provider-neutral domain models shared by connectors, agents, and persistence."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EmailAddress = Annotated[str, Field(max_length=320)]
Label = Annotated[str, Field(max_length=255)]
Indicator = Annotated[str, Field(max_length=240)]
KeyPoint = Annotated[str, Field(max_length=1000)]
Deadline = Annotated[str, Field(max_length=128)]


class AttachmentInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(default="", max_length=512)
    content_type: str = Field(default="application/octet-stream", max_length=255)
    size: int | None = Field(default=None, ge=0)
    inline: bool = False


class EmailEnvelope(BaseModel):
    """Sanitized email data. Attachment bytes and remote resources are never loaded."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=128)
    provider: Literal["gmail", "outlook"]
    provider_message_id: str = Field(min_length=1, max_length=1024)
    thread_id: str | None = Field(default=None, max_length=1024)
    subject: str = Field(default="", max_length=998)
    sender_name: str = Field(default="", max_length=320)
    sender_address: str = Field(default="", max_length=320)
    recipients: list[EmailAddress] = Field(default_factory=list, max_length=200)
    cc: list[EmailAddress] = Field(default_factory=list, max_length=200)
    received_at: datetime
    body_text: str = Field(default="", max_length=500_000)
    snippet: str = Field(default="", max_length=2_000)
    attachments: list[AttachmentInfo] = Field(default_factory=list, max_length=100)
    labels: list[Label] = Field(default_factory=list, max_length=200)
    importance: Literal["low", "normal", "high"] = "normal"
    is_read: bool | None = None
    web_url: str | None = Field(default=None, max_length=4096)

    @field_validator("received_at")
    @classmethod
    def ensure_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def content_hash(self) -> str:
        canonical = {
            "attachments": [item.model_dump(mode="json") for item in self.attachments],
            "body_text": self.body_text,
            "provider": self.provider,
            "provider_message_id": self.provider_message_id,
            "sender_address": self.sender_address,
            "subject": self.subject,
        }
        payload = json.dumps(
            canonical,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="replace")
        return hashlib.sha256(payload).hexdigest()

    def message_ref(self) -> str:
        return f"{self.provider}:{self.account_id}:{self.provider_message_id}"


class FetchDeadLetter(BaseModel):
    """A bounded, content-free record for one permanently unreadable provider message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    provider: Literal["gmail", "outlook"]
    provider_message_id: str = Field(min_length=1, max_length=1024)
    code: str = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )

    def message_ref(self) -> str:
        return f"{self.provider}:{self.account_id}:{self.provider_message_id}"


class FetchBatch(BaseModel):
    """One provider page and its opaque continuation/checkpoint."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    messages: list[EmailEnvelope] = Field(max_length=500)
    dead_letters: list[FetchDeadLetter] = Field(default_factory=list, max_length=500)
    next_cursor: str | None
    cursor_reset: bool = False

    @model_validator(mode="after")
    def validate_message_outcomes(self) -> FetchBatch:
        refs = [
            (message.provider, message.account_id, message.provider_message_id)
            for message in self.messages
        ]
        refs.extend(
            (
                dead_letter.provider,
                dead_letter.account_id,
                dead_letter.provider_message_id,
            )
            for dead_letter in self.dead_letters
        )
        if len(refs) > 500:
            raise ValueError("a fetch batch cannot contain more than 500 message outcomes")
        if len(refs) != len(set(refs)):
            raise ValueError("a fetch batch cannot contain duplicate message outcomes")
        return self


RiskLevel = Literal["none", "low", "medium", "high"]


class SafetyAssessment(BaseModel):
    risk_level: RiskLevel
    prompt_injection_detected: bool = False
    phishing_likelihood: float = Field(ge=0.0, le=1.0)
    indicators: list[Indicator] = Field(default_factory=list, max_length=12)
    rationale: str = Field(max_length=1200)
    confidence: float = Field(ge=0.0, le=1.0)


EmailCategory = Literal[
    "action_required",
    "informational",
    "newsletter",
    "receipt",
    "security",
    "spam",
    "other",
]
Priority = Literal["low", "normal", "high", "critical"]


class TriageResult(BaseModel):
    category: EmailCategory
    priority: Priority
    needs_human_review: bool
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(max_length=1200)


class SummaryResult(BaseModel):
    summary: str = Field(max_length=4000)
    key_points: list[KeyPoint] = Field(default_factory=list, max_length=12)
    detected_language: str = Field(default="unknown", max_length=32)
    restricted: bool = False


class ActionItem(BaseModel):
    description: str = Field(max_length=1200)
    owner: str | None = Field(default=None, max_length=320)
    due_date: str | None = Field(default=None, max_length=64)
    confidence: float = Field(ge=0.0, le=1.0)


class TaskExtractionResult(BaseModel):
    action_items: list[ActionItem] = Field(default_factory=list, max_length=20)
    explicit_deadlines: list[Deadline] = Field(default_factory=list, max_length=20)


class EmailAnalysis(BaseModel):
    analysis_id: str = Field(min_length=1, max_length=64)
    message_ref: str = Field(min_length=1, max_length=1400)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    pipeline_version: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=64)
    created_at: datetime
    category: EmailCategory
    priority: Priority
    needs_human_review: bool
    summary: str = Field(max_length=4000)
    key_points: list[KeyPoint] = Field(default_factory=list, max_length=12)
    action_items: list[ActionItem] = Field(default_factory=list, max_length=20)
    safety: SafetyAssessment
    triage: TriageResult | None = None
    detected_language: str = Field(default="unknown", max_length=32)

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class AnalysisPage(BaseModel):
    """A bounded page of completed analyses and the total matching row count."""

    model_config = ConfigDict(extra="forbid")

    items: list[EmailAnalysis] = Field(default_factory=list, max_length=100)
    total: int = Field(ge=0)


class SyncReport(BaseModel):
    account_id: str
    provider: Literal["gmail", "outlook"]
    fetched: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    dead_lettered: int = 0
    cursor_advanced: bool = False
    cursor_reset: bool = False
    analyses: list[EmailAnalysis] = Field(default_factory=list)

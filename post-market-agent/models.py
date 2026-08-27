from datetime import datetime
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field


class InputRaw(BaseModel):
    name: str = Field(description="The name of the lead")
    email: str = Field(description="The email of the lead")
    company: str = Field(description="The company of the lead")
    job_title: str = Field(description="The job title of the lead")
    phone: Optional[str] = Field(description="The phone number of the lead")
    country: Optional[str] = Field(description="The country of the lead")
    message: Optional[str] = Field(description="The message of the lead")
    source: Optional[str] = Field(description="The source of the lead")
    submitted_at: Optional[datetime] = Field(
        description="The date and time the lead was submitted")


class QualificationStatus(StrEnum):
    sales_qualified = "sales_qualified"
    marketing_qualified = "marketing_qualified"
    product_qualified = "product_qualified"
    customer_expansion = "customer_expansion"
    partner_lead = "partner_lead"
    needs_review = "needs_review"
    duplicate = "duplicate"
    disqualified = "disqualified"


class Priority(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"


class NormalizedLead(BaseModel):
    name: str = ""
    email: str = ""
    domain: str = ""
    company: str = ""
    title: str = ""
    seniority: str = ""
    department: str = ""
    region: str = ""
    personal_email: bool = False


class CompanyEnrichment(BaseModel):
    name: str | None = None
    domain: str | None = None
    industry: str | None = None
    employee_count: int | None = None
    revenue_range: str | None = None
    hq_country: str | None = None
    target_account: bool = False


class CrmEnrichment(BaseModel):
    existing_contact: bool = False
    existing_account: bool = False
    existing_customer: bool = False
    open_opportunity: bool = False
    account_owner: str | None = None


class BehaviorEnrichment(BaseModel):
    source: str | None = None
    pages_viewed: list[str] = Field(default_factory=list)
    product_usage_signals: list[str] = Field(default_factory=list)
    campaign_engagement: list[str] = Field(default_factory=list)


class Enrichment(BaseModel):
    company: CompanyEnrichment = Field(default_factory=CompanyEnrichment)
    crm: CrmEnrichment = Field(default_factory=CrmEnrichment)
    behavior: BehaviorEnrichment = Field(default_factory=BehaviorEnrichment)


class Scores(BaseModel):
    fit: int = Field(ge=0, le=100)
    intent: int = Field(ge=0, le=100)
    authority: int = Field(ge=0, le=100)
    engagement: int = Field(ge=0, le=100)
    data_quality: int = Field(ge=0, le=100)
    risk_penalty: int = Field(ge=0, le=100)
    overall: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)


class Classification(BaseModel):
    status: QualificationStatus
    priority: Priority
    reason_summary: str


class Routing(BaseModel):
    team: str = ""
    owner: str = ""
    queue: str = ""
    sla: str = ""
    next_action: str = ""


class VisibleReasoningTraceItem(BaseModel):
    step: str
    finding: str
    evidence: str
    score_impact: str = "0"
    confidence: float | None = Field(default=None, ge=0, le=1)


class Audit(BaseModel):
    data_sources_used: list[str] = Field(default_factory=list)
    fields_updated: list[str] = Field(default_factory=list)
    human_review_required: bool = False
    review_reason: str = ""


class LeadQualificationOutput(BaseModel):
    lead_id: str = ""
    normalized_lead: NormalizedLead
    enrichment: Enrichment
    scores: Scores
    classification: Classification
    routing: Routing
    visible_reasoning_trace: list[VisibleReasoningTraceItem] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    recommended_questions: list[str] = Field(default_factory=list)
    audit: Audit = Field(default_factory=Audit)
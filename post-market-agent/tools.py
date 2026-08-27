import os
import re
from typing import Any, Literal
from urllib.parse import quote

import requests
from langchain_core.tools import tool
from pydantic import BaseModel, Field


class ToolConfigurationError(RuntimeError):
    """Raised when a provider-backed tool has not been configured."""


JSONDict = dict[str, Any]


SUPPORTED_COUNTRIES = {
    "united states",
    "usa",
    "us",
    "canada",
    "united kingdom",
    "uk",
    "ireland",
    "france",
    "germany",
    "netherlands",
    "australia",
}
TARGET_INDUSTRIES = {"b2b saas", "software", "technology", "fintech", "healthcare"}
TECH_STACK_MATCHES = {"salesforce", "zendesk", "snowflake", "hubspot", "segment"}
PERSONAL_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "icloud.com",
    "aol.com",
}


class EmailInput(BaseModel):
    email: str = Field(description="Email address for the person or lead lookup.")


class DomainOrCompanyInput(BaseModel):
    domain_or_company_name: str = Field(
        description="Company domain or company name to match in CRM."
    )


class ExistingLeadSearchInput(BaseModel):
    email: str = Field(description="Lead email address.")
    domain: str | None = Field(default=None, description="Company email domain.")


class AccountIdInput(BaseModel):
    account_id: str = Field(description="CRM account identifier.")


class CompanyEnrichmentInput(BaseModel):
    domain: str = Field(description="Company domain to enrich.")


class UsageInput(BaseModel):
    email_or_account_id: str = Field(description="Email address or account ID.")


class LeadScoreInput(BaseModel):
    lead: JSONDict = Field(description="Raw or normalized lead fields.")
    enrichment: JSONDict = Field(description="Verified and inferred enrichment fields.")
    behavior: JSONDict = Field(default_factory=dict, description="Behavioral signals.")


class RouteLeadInput(BaseModel):
    score: int = Field(ge=0, le=100, description="Overall lead score.")
    employee_count: int | None = Field(default=None, description="Company employee count.")
    region: str | None = Field(default=None, description="Normalized sales region.")
    existing_customer: bool = Field(default=False, description="Whether account is a customer.")
    open_opportunity: bool = Field(default=False, description="Whether an opportunity exists.")
    account_owner: str | None = Field(default=None, description="Named account owner.")
    target_account: bool = Field(default=False, description="Whether account is strategic.")
    product_interest: str | None = Field(default=None, description="Detected product interest.")


class CreateCrmTaskInput(BaseModel):
    owner: str = Field(description="Task owner or queue.")
    lead: JSONDict = Field(description="Lead payload.")
    next_action: str = Field(description="Recommended next action.")


class SlackAlertInput(BaseModel):
    channel: str = Field(description="Slack channel or routing label.")
    summary: str = Field(description="Alert text.")


class UpdateCrmLeadInput(BaseModel):
    lead_id: str = Field(description="CRM lead identifier.")
    fields: JSONDict = Field(description="Fields to update.")


def _provider_config(service: str) -> tuple[str, dict[str, str]]:
    prefix = service.upper()
    base_url = os.getenv(f"{prefix}_API_BASE_URL")
    api_key = os.getenv(f"{prefix}_API_KEY")
    if not base_url:
        raise ToolConfigurationError(
            f"{prefix}_API_BASE_URL is required for provider-backed {service} tools."
        )
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return base_url.rstrip("/"), headers


def _request_json(
    service: Literal["crm", "enrichment", "product_analytics", "marketing"],
    method: str,
    path: str,
    *,
    params: JSONDict | None = None,
    json_body: JSONDict | None = None,
) -> JSONDict:
    base_url, headers = _provider_config(service)
    response = requests.request(
        method,
        f"{base_url}{path}",
        params={key: value for key, value in (params or {}).items() if value is not None},
        json=json_body,
        headers=headers,
        timeout=15,
    )
    response.raise_for_status()
    if not response.content:
        return {}
    return response.json()


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].lower()


def _add_factor(
    factors: list[JSONDict],
    signal: str,
    value: Any,
    impact: int,
    reason: str,
) -> int:
    factors.append(
        {
            "signal": signal,
            "value": value,
            "impact": f"{impact:+d}",
            "reason": reason,
        }
    )
    return impact


def _clamp_score(score: float) -> int:
    return max(0, min(100, round(score)))


def _score_fit(lead: JSONDict, enrichment: JSONDict) -> tuple[int, list[JSONDict]]:
    factors: list[JSONDict] = []
    score = 0
    industry = _lower(enrichment.get("industry"))
    employee_count = enrichment.get("employee_count")
    country = _lower(enrichment.get("hq_country") or lead.get("country"))
    tech_stack = {_lower(item) for item in enrichment.get("tech_stack", [])}

    if industry in TARGET_INDUSTRIES:
        score += _add_factor(factors, "Target industry", industry, 15, "Matches ICP industry.")
    if isinstance(employee_count, int) and employee_count >= 1000:
        score += _add_factor(
            factors, "Company size", employee_count, 20, "Matches enterprise ICP."
        )
    elif isinstance(employee_count, int) and employee_count < 50:
        score += _add_factor(factors, "Wrong segment", employee_count, -20, "Below ICP size.")
    if enrichment.get("target_account") is True:
        score += _add_factor(
            factors, "Target account", True, 20, "Company is on the strategic account list."
        )
    if country in SUPPORTED_COUNTRIES:
        score += _add_factor(
            factors, "Supported geography", country, 10, "Country maps to a supported sales region."
        )
    elif country:
        score += _add_factor(
            factors, "Unsupported geography", country, -25, "Country is outside supported regions."
        )
    matches = sorted(tech_stack & TECH_STACK_MATCHES)
    if matches:
        score += _add_factor(
            factors,
            "Existing tech stack match",
            matches,
            10,
            "Known adjacent systems suggest implementation fit.",
        )
    return _clamp_score(score), factors


def _score_intent(lead: JSONDict, behavior: JSONDict) -> tuple[int, list[JSONDict]]:
    factors: list[JSONDict] = []
    score = 0
    source = _lower(lead.get("source"))
    message = _lower(lead.get("message"))
    pages = {_lower(page) for page in behavior.get("pages_viewed", [])}
    email_engagement = behavior.get("email_engagement", {}) or {}
    product_usage = behavior.get("product_usage", {}) or {}

    if source == "demo_request":
        score += _add_factor(factors, "Demo request", source, 30, "Direct sales request.")
    if any("/pricing" in page for page in pages):
        score += _add_factor(
            factors, "Pricing page viewed", "/pricing", 15, "Pricing activity suggests evaluation."
        )
    if any("/enterprise" in page for page in pages):
        score += _add_factor(
            factors,
            "Enterprise page viewed",
            "/enterprise",
            10,
            "Enterprise content indicates a larger buying motion.",
        )
    if any(term in message for term in ("automation", "workflow", "support ops", "evaluating")):
        score += _add_factor(
            factors,
            "Specific pain described",
            lead.get("message"),
            15,
            "Lead described a concrete business use case.",
        )
    if email_engagement.get("clicked_pricing_link") is True:
        score += _add_factor(
            factors,
            "Clicked pricing link",
            True,
            10,
            "Campaign engagement reinforces active evaluation.",
        )
    if int(product_usage.get("created_workflows") or 0) > 0:
        score += _add_factor(
            factors,
            "Product activation activity",
            product_usage.get("created_workflows"),
            15,
            "Product usage suggests hands-on evaluation.",
        )
    if "just browsing" in message:
        score += _add_factor(factors, "Just browsing language", lead.get("message"), -10, "Low urgency.")
    return _clamp_score(score), factors


def _score_authority(lead: JSONDict) -> tuple[int, list[JSONDict]]:
    factors: list[JSONDict] = []
    title = _lower(lead.get("job_title") or lead.get("title"))
    score = 35
    if re.search(r"\b(founder|ceo|coo|cio|cto|cfo|chief)\b", title):
        score = _add_factor(factors, "Executive title", title, 90, "Likely executive sponsor.")
    elif re.search(r"\b(vp|vice president|head of|director)\b", title):
        score = _add_factor(factors, "VP/director-level title", title, 80, "Likely buying influence.")
    elif "manager" in title:
        score = _add_factor(factors, "Manager title", title, 55, "May influence evaluation.")
    elif re.search(r"\b(student|intern)\b", title):
        score = _add_factor(factors, "Low-authority title", title, 10, "Likely not a buyer.")
    else:
        factors.append(
            {
                "signal": "Unknown authority",
                "value": title or None,
                "impact": "+35",
                "reason": "Title does not clearly indicate seniority.",
            }
        )
    return _clamp_score(score), factors


def _score_engagement(behavior: JSONDict) -> tuple[int, list[JSONDict]]:
    factors: list[JSONDict] = []
    score = 0
    email_engagement = behavior.get("email_engagement", {}) or {}
    product_usage = behavior.get("product_usage", {}) or {}
    pages = behavior.get("pages_viewed", []) or []

    if email_engagement.get("opened_last_campaign") is True:
        score += _add_factor(factors, "Opened campaign", True, 10, "Recent marketing engagement.")
    if email_engagement.get("clicked_pricing_link") is True:
        score += _add_factor(factors, "Clicked campaign", True, 15, "Clicked a commercial CTA.")
    if len(pages) >= 3:
        score += _add_factor(factors, "Multi-page session", len(pages), 15, "Broad site engagement.")
    active_users = int(product_usage.get("active_users") or 0)
    if active_users >= 5:
        score += _add_factor(
            factors, "Multiple active users", active_users, 20, "Team usage suggests account interest."
        )
    created_workflows = int(product_usage.get("created_workflows") or 0)
    if created_workflows > 0:
        score += _add_factor(
            factors, "Created workflows", created_workflows, 20, "Workflow creation indicates activation."
        )
    return _clamp_score(score), factors


def _score_data_quality(lead: JSONDict, enrichment: JSONDict) -> tuple[int, list[str], list[JSONDict]]:
    missing: list[str] = []
    factors: list[JSONDict] = []
    score = 100
    domain = enrichment.get("company_domain") or _email_domain(lead.get("email"))

    if not domain:
        score -= 25
        missing.append("company domain")
        factors.append({"signal": "Missing domain", "impact": "-25"})
    elif domain in PERSONAL_EMAIL_DOMAINS:
        score -= 20
        factors.append({"signal": "Personal email domain", "impact": "-20"})
    for field, label in (
        ("employee_count", "company size"),
        ("industry", "industry"),
    ):
        if enrichment.get(field) in (None, ""):
            score -= 10
            missing.append(label)
            factors.append({"signal": f"Missing {label}", "impact": "-10"})
    for required in ("budget", "timeline", "current vendor"):
        if not lead.get(required) and not enrichment.get(required):
            missing.append(required)
    return _clamp_score(score), missing, factors


def _score_risk(lead: JSONDict, enrichment: JSONDict) -> tuple[int, list[JSONDict]]:
    factors: list[JSONDict] = []
    penalty = 0
    domain = _email_domain(lead.get("email"))
    message = _lower(lead.get("message"))
    title = _lower(lead.get("job_title") or lead.get("title"))

    if domain in PERSONAL_EMAIL_DOMAINS and not enrichment.get("company_domain"):
        penalty += abs(_add_factor(factors, "Personal email", domain, -15, "Company is not verified."))
    if enrichment.get("existing_customer") is True:
        penalty += abs(
            _add_factor(
                factors,
                "Existing customer",
                True,
                -20,
                "Should route to customer expansion or success.",
            )
        )
    if enrichment.get("open_opportunity") is True:
        penalty += abs(
            _add_factor(
                factors,
                "Open opportunity",
                True,
                -20,
                "Must avoid duplicate opportunity creation.",
            )
        )
    if re.search(r"\b(student|intern)\b", title):
        penalty += abs(_add_factor(factors, "Student or intern", title, -15, "Likely low buying authority."))
    if re.search(r"\b(seo|crypto|casino|loan|free money)\b", message):
        penalty += abs(_add_factor(factors, "Spam language", lead.get("message"), -30, "Message contains spam-like terms."))
    return _clamp_score(penalty), factors


@tool(args_schema=EmailInput)
def lookup_crm_contact(email: str) -> JSONDict:
    """Look up a CRM contact by email using the configured CRM provider."""
    return _request_json("crm", "GET", "/contacts", params={"email": email})


@tool(args_schema=DomainOrCompanyInput)
def lookup_crm_account(domain_or_company_name: str) -> JSONDict:
    """Look up a CRM account by company domain or company name."""
    return _request_json(
        "crm", "GET", "/accounts", params={"q": domain_or_company_name}
    )


@tool(args_schema=ExistingLeadSearchInput)
def search_existing_leads(email: str, domain: str | None = None) -> JSONDict:
    """Search CRM leads for duplicate recent submissions."""
    return _request_json("crm", "GET", "/leads", params={"email": email, "domain": domain})


@tool(args_schema=AccountIdInput)
def get_account_owner(account_id: str) -> JSONDict:
    """Fetch the named owner for a CRM account."""
    return _request_json("crm", "GET", f"/accounts/{quote(account_id)}/owner")


@tool(args_schema=AccountIdInput)
def get_open_opportunities(account_id: str) -> JSONDict:
    """Fetch open opportunities associated with a CRM account."""
    return _request_json("crm", "GET", f"/accounts/{quote(account_id)}/opportunities")


@tool(args_schema=CompanyEnrichmentInput)
def enrich_company(domain: str) -> JSONDict:
    """Fetch verified company enrichment for a domain from the configured provider."""
    return _request_json("enrichment", "GET", "/companies", params={"domain": domain})


@tool(args_schema=EmailInput)
def enrich_person(email: str) -> JSONDict:
    """Fetch verified person enrichment for an email from the configured provider."""
    return _request_json("enrichment", "GET", "/people", params={"email": email})


@tool(args_schema=UsageInput)
def get_product_usage(email_or_account_id: str) -> JSONDict:
    """Fetch product usage signals from the configured product analytics provider."""
    return _request_json(
        "product_analytics", "GET", "/usage", params={"identity": email_or_account_id}
    )


@tool(args_schema=EmailInput)
def get_marketing_engagement(email: str) -> JSONDict:
    """Fetch marketing engagement signals from the configured marketing provider."""
    return _request_json("marketing", "GET", "/engagement", params={"email": email})


@tool(args_schema=LeadScoreInput)
def score_lead(
    lead: JSONDict,
    enrichment: JSONDict,
    behavior: JSONDict | None = None,
) -> JSONDict:
    """Score lead fit, intent, authority, engagement, data quality, and risk."""
    behavior = behavior or {}
    fit, fit_factors = _score_fit(lead, enrichment)
    intent, intent_factors = _score_intent(lead, behavior)
    authority, authority_factors = _score_authority(lead)
    engagement, engagement_factors = _score_engagement(behavior)
    data_quality, missing_information, data_quality_factors = _score_data_quality(
        lead, enrichment
    )
    risk_penalty, risk_factors = _score_risk(lead, enrichment)
    overall = _clamp_score(
        (fit * 0.30)
        + (intent * 0.25)
        + (authority * 0.20)
        + (engagement * 0.15)
        + (data_quality * 0.10)
        - risk_penalty
    )
    confidence = max(0.0, min(1.0, round((data_quality / 100) - (risk_penalty / 200), 2)))

    return {
        "fit": fit,
        "intent": intent,
        "authority": authority,
        "engagement": engagement,
        "data_quality": data_quality,
        "risk_penalty": risk_penalty,
        "overall": overall,
        "confidence": confidence,
        "fit_factors": fit_factors,
        "intent_factors": intent_factors,
        "authority_factors": authority_factors,
        "engagement_factors": engagement_factors,
        "data_quality_factors": data_quality_factors,
        "risk_factors": risk_factors,
        "missing_information": missing_information,
    }


@tool(args_schema=RouteLeadInput)
def route_lead(
    score: int,
    employee_count: int | None = None,
    region: str | None = None,
    existing_customer: bool = False,
    open_opportunity: bool = False,
    account_owner: str | None = None,
    target_account: bool = False,
    product_interest: str | None = None,
) -> JSONDict:
    """Route a scored lead with deterministic account-conflict and segment rules."""
    region_name = region or "Unassigned"
    if open_opportunity:
        return {
            "status": "duplicate",
            "priority": "urgent",
            "team": "Current Opportunity Owner",
            "owner": account_owner or "Current opportunity owner",
            "queue": "Existing Opportunity",
            "sla": "15 minutes",
            "next_action": "Notify the current opportunity owner and do not create a duplicate opportunity.",
            "human_review_required": True,
            "review_reason": "Open opportunity already exists for this account.",
        }
    if existing_customer:
        return {
            "status": "customer_expansion",
            "priority": "high",
            "team": "Customer Success",
            "owner": account_owner or "Customer Success Queue",
            "queue": "Customer Expansion",
            "sla": "4 business hours",
            "next_action": "Route to CSM or expansion AE for account-safe follow-up.",
            "human_review_required": True,
            "review_reason": "Current customer should not enter new-business routing.",
        }
    if target_account and score >= 70:
        return {
            "status": "sales_qualified",
            "priority": "high",
            "team": "Strategic Accounts",
            "owner": account_owner or f"{region_name} Strategic Accounts Queue",
            "queue": "Strategic Accounts",
            "sla": "15 minutes",
            "next_action": f"Follow up with a personalized message about {product_interest or 'the stated use case'}.",
            "human_review_required": False,
            "review_reason": "",
        }
    if score >= 80 and (employee_count or 0) >= 1000:
        team = "Enterprise SDR"
        queue = f"{region_name} Enterprise SDR Queue"
        priority = "high"
        sla = "15 minutes"
        status = "sales_qualified"
    elif score >= 60:
        team = "Commercial SDR"
        queue = f"{region_name} Commercial SDR Queue"
        priority = "medium"
        sla = "1 business day"
        status = "sales_qualified"
    elif score >= 40:
        team = "Marketing"
        queue = "Marketing Nurture"
        priority = "low"
        sla = "3 business days"
        status = "marketing_qualified"
    else:
        team = "Marketing"
        queue = "Suppression Review"
        priority = "low"
        sla = "No SLA"
        status = "disqualified"

    return {
        "status": status,
        "priority": priority,
        "team": team,
        "owner": account_owner or queue,
        "queue": queue,
        "sla": sla,
        "next_action": f"Follow up based on {product_interest or 'the strongest qualified signal'}.",
        "human_review_required": 65 <= score <= 74,
        "review_reason": "Score is close to SQL threshold." if 65 <= score <= 74 else "",
    }


@tool(args_schema=CreateCrmTaskInput)
def create_crm_task(owner: str, lead: JSONDict, next_action: str) -> JSONDict:
    """Create a CRM task for the configured CRM provider."""
    return _request_json(
        "crm",
        "POST",
        "/tasks",
        json_body={"owner": owner, "lead": lead, "next_action": next_action},
    )


@tool(args_schema=SlackAlertInput)
def post_slack_alert(channel: str, summary: str) -> JSONDict:
    """Post a routing alert to the configured Slack webhook."""
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        raise ToolConfigurationError("SLACK_WEBHOOK_URL is required for Slack alerts.")
    response = requests.post(
        webhook_url,
        json={"channel": channel, "text": summary},
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
    response.raise_for_status()
    if not response.content:
        return {}
    return response.json()


@tool(args_schema=UpdateCrmLeadInput)
def update_crm_lead(lead_id: str, fields: JSONDict) -> JSONDict:
    """Update CRM lead fields through the configured CRM provider."""
    return _request_json(
        "crm", "PATCH", f"/leads/{quote(lead_id)}", json_body={"fields": fields}
    )


LEAD_QUALIFICATION_TOOLS = [
    lookup_crm_contact,
    lookup_crm_account,
    search_existing_leads,
    get_account_owner,
    get_open_opportunities,
    enrich_company,
    enrich_person,
    get_product_usage,
    get_marketing_engagement,
    score_lead,
    route_lead,
    create_crm_task,
    post_slack_alert,
    update_crm_lead,
]

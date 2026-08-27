import re
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from tools import route_lead, score_lead


JSONDict = dict[str, Any]


class LeadQualificationState(TypedDict, total=False):
    lead: JSONDict
    behavior: JSONDict
    enrichment: JSONDict
    normalized_lead: JSONDict
    scores: JSONDict
    routing: JSONDict
    visible_reasoning_trace: list[JSONDict]
    final_output: JSONDict


PERSONAL_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "icloud.com",
    "aol.com",
}


def _email_domain(email: str | None) -> str:
    if not email or "@" not in email:
        return ""
    return email.rsplit("@", 1)[1].strip().lower()


def _normalize_company_name(company: str | None) -> str:
    company = (company or "").strip()
    company = re.sub(r"\b(inc|llc|ltd|corp|corporation)\.?\b", "", company, flags=re.I)
    company = re.sub(r"[^A-Za-z0-9]+", " ", company)
    return "".join(part.capitalize() for part in company.split())


def _infer_seniority(title: str | None) -> str:
    title = (title or "").lower()
    if re.search(r"\b(founder|ceo|coo|cio|cto|cfo|chief|vp|vice president)\b", title):
        return "executive"
    if re.search(r"\b(head of|director)\b", title):
        return "director"
    if "manager" in title:
        return "manager"
    if re.search(r"\b(student|intern)\b", title):
        return "student"
    return "unknown"


def _infer_department(title: str | None, message: str | None) -> str:
    text = f"{title or ''} {message or ''}".lower()
    department_terms = {
        "operations": ("operations", "ops", "workflow"),
        "sales": ("sales", "revenue", "gtm"),
        "marketing": ("marketing", "campaign", "demand"),
        "support": ("support", "customer service", "helpdesk"),
        "engineering": ("engineering", "developer", "technical"),
        "security": ("security", "compliance", "risk"),
    }
    for department, terms in department_terms.items():
        if any(term in text for term in terms):
            return department
    return "unknown"


def _region_for_country(country: str | None) -> str:
    country = (country or "").strip().lower()
    if country in {"united states", "usa", "us", "canada"}:
        return "North America"
    if country in {"united kingdom", "uk", "ireland", "france", "germany", "netherlands"}:
        return "EMEA"
    if country in {"australia", "new zealand", "singapore", "japan"}:
        return "APAC"
    return "Unassigned"


def _normalize_node(state: LeadQualificationState) -> LeadQualificationState:
    lead = state.get("lead", {})
    enrichment = state.get("enrichment", {})
    email = (lead.get("email") or "").strip().lower()
    domain = enrichment.get("company_domain") or _email_domain(email)
    country = enrichment.get("hq_country") or lead.get("country")
    normalized = {
        "name": lead.get("name") or " ".join(
            part for part in [lead.get("first_name"), lead.get("last_name")] if part
        ),
        "email": email,
        "domain": domain,
        "company": _normalize_company_name(lead.get("company") or enrichment.get("company_name")),
        "title": lead.get("job_title") or lead.get("title") or "",
        "seniority": _infer_seniority(lead.get("job_title") or lead.get("title")),
        "department": _infer_department(
            lead.get("job_title") or lead.get("title"), lead.get("message")
        ),
        "region": _region_for_country(country),
        "personal_email": domain in PERSONAL_EMAIL_DOMAINS,
    }
    trace = [
        {
            "step": "Normalize lead",
            "finding": "Standardized identity, company, title, and region fields.",
            "evidence": f"Email domain: {domain or 'missing'}; region: {normalized['region']}",
            "score_impact": "0",
            "confidence": 0.9 if domain else 0.4,
        }
    ]
    return {**state, "normalized_lead": normalized, "visible_reasoning_trace": trace}


def _score_node(state: LeadQualificationState) -> LeadQualificationState:
    lead = {**state.get("lead", {}), **state.get("normalized_lead", {})}
    scores = score_lead.invoke(
        {
            "lead": lead,
            "enrichment": state.get("enrichment", {}),
            "behavior": state.get("behavior", {}),
        }
    )
    trace = state.get("visible_reasoning_trace", []) + [
        {
            "step": "Score lead",
            "finding": "Calculated fit, intent, authority, engagement, data quality, and risk.",
            "evidence": (
                f"Fit {scores['fit']}; intent {scores['intent']}; "
                f"authority {scores['authority']}; risk penalty {scores['risk_penalty']}"
            ),
            "score_impact": str(scores["overall"]),
            "confidence": scores["confidence"],
        }
    ]
    return {**state, "scores": scores, "visible_reasoning_trace": trace}


def _route_node(state: LeadQualificationState) -> LeadQualificationState:
    normalized = state.get("normalized_lead", {})
    enrichment = state.get("enrichment", {})
    scores = state.get("scores", {})
    routing = route_lead.invoke(
        {
            "score": scores.get("overall", 0),
            "employee_count": enrichment.get("employee_count"),
            "region": normalized.get("region"),
            "existing_customer": bool(enrichment.get("existing_customer", False)),
            "open_opportunity": bool(enrichment.get("open_opportunity", False)),
            "account_owner": enrichment.get("account_owner"),
            "target_account": bool(enrichment.get("target_account", False)),
            "product_interest": enrichment.get("product_interest")
            or _likely_use_case(state.get("lead", {})),
        }
    )
    trace = state.get("visible_reasoning_trace", []) + [
        {
            "step": "Routing decision",
            "finding": f"Routed to {routing['team']}.",
            "evidence": routing["review_reason"] or f"Queue: {routing['queue']}; SLA: {routing['sla']}",
            "score_impact": "0",
            "confidence": scores.get("confidence", 0),
        }
    ]
    return {**state, "routing": routing, "visible_reasoning_trace": trace}


def _likely_use_case(lead: JSONDict) -> str:
    message = (lead.get("message") or "").lower()
    if "support" in message and ("workflow" in message or "automation" in message):
        return "support operations workflow automation"
    if "workflow" in message or "automation" in message:
        return "workflow automation"
    return "stated use case"


def _assemble_node(state: LeadQualificationState) -> LeadQualificationState:
    lead = state.get("lead", {})
    enrichment = state.get("enrichment", {})
    behavior = state.get("behavior", {})
    scores = state.get("scores", {})
    routing = state.get("routing", {})
    status = routing.get("status", "needs_review")
    output = {
        "lead_id": lead.get("lead_id") or lead.get("id") or "",
        "normalized_lead": state.get("normalized_lead", {}),
        "enrichment": {
            "company": {
                "name": enrichment.get("company_name") or state.get("normalized_lead", {}).get("company"),
                "domain": enrichment.get("company_domain") or state.get("normalized_lead", {}).get("domain"),
                "industry": enrichment.get("industry"),
                "employee_count": enrichment.get("employee_count"),
                "revenue_range": enrichment.get("annual_revenue_estimate")
                or enrichment.get("revenue_range"),
                "hq_country": enrichment.get("hq_country") or lead.get("country"),
                "target_account": bool(enrichment.get("target_account", False)),
            },
            "crm": {
                "existing_contact": bool(enrichment.get("existing_contact", False)),
                "existing_account": bool(enrichment.get("existing_account", False)),
                "existing_customer": bool(enrichment.get("existing_customer", False)),
                "open_opportunity": bool(enrichment.get("open_opportunity", False)),
                "account_owner": enrichment.get("account_owner"),
            },
            "behavior": {
                "source": lead.get("source"),
                "pages_viewed": behavior.get("pages_viewed", []),
                "product_usage_signals": _product_usage_signals(behavior.get("product_usage", {})),
                "campaign_engagement": _campaign_signals(behavior.get("email_engagement", {})),
            },
        },
        "scores": {
            "fit": scores.get("fit", 0),
            "intent": scores.get("intent", 0),
            "authority": scores.get("authority", 0),
            "engagement": scores.get("engagement", 0),
            "data_quality": scores.get("data_quality", 0),
            "risk_penalty": scores.get("risk_penalty", 0),
            "overall": scores.get("overall", 0),
            "confidence": scores.get("confidence", 0),
        },
        "classification": {
            "status": status,
            "priority": routing.get("priority", "low"),
            "reason_summary": _reason_summary(status, scores, routing),
        },
        "routing": {
            "team": routing.get("team", ""),
            "owner": routing.get("owner", ""),
            "queue": routing.get("queue", ""),
            "sla": routing.get("sla", ""),
            "next_action": routing.get("next_action", ""),
        },
        "visible_reasoning_trace": _visible_trace(state),
        "missing_information": scores.get("missing_information", []),
        "recommended_questions": _recommended_questions(scores.get("missing_information", [])),
        "audit": {
            "data_sources_used": _data_sources_used(enrichment, behavior),
            "fields_updated": [],
            "human_review_required": bool(routing.get("human_review_required", False)),
            "review_reason": routing.get("review_reason", ""),
        },
    }
    return {**state, "final_output": output}


def _visible_trace(state: LeadQualificationState) -> list[JSONDict]:
    scores = state.get("scores", {})
    trace = list(state.get("visible_reasoning_trace", []))
    for label, factors_key in (
        ("ICP fit", "fit_factors"),
        ("Intent assessment", "intent_factors"),
        ("Authority assessment", "authority_factors"),
        ("Engagement assessment", "engagement_factors"),
        ("Risk check", "risk_factors"),
    ):
        factors = scores.get(factors_key, [])
        if factors:
            factor = factors[0]
            trace.append(
                {
                    "step": label,
                    "finding": factor.get("reason", ""),
                    "evidence": f"{factor.get('signal')}: {factor.get('value')}",
                    "score_impact": factor.get("impact", "0"),
                    "confidence": scores.get("confidence", 0),
                }
            )
    return trace


def _product_usage_signals(product_usage: JSONDict) -> list[str]:
    signals: list[str] = []
    if product_usage.get("active_users"):
        signals.append(f"{product_usage['active_users']} active users")
    if product_usage.get("created_workflows"):
        signals.append(f"{product_usage['created_workflows']} created workflows")
    return signals


def _campaign_signals(email_engagement: JSONDict) -> list[str]:
    signals: list[str] = []
    if email_engagement.get("opened_last_campaign"):
        signals.append("opened last campaign")
    if email_engagement.get("clicked_pricing_link"):
        signals.append("clicked pricing link")
    return signals


def _reason_summary(status: str, scores: JSONDict, routing: JSONDict) -> str:
    if status == "customer_expansion":
        return "Existing customer lead routed away from new-business sales for expansion review."
    if status == "duplicate":
        return "Lead is tied to an existing open opportunity and should not create duplicate sales motion."
    if status == "sales_qualified":
        return (
            "Lead qualifies for sales follow-up based on score "
            f"{scores.get('overall', 0)} and route {routing.get('queue', '')}."
        )
    if status == "marketing_qualified":
        return "Lead has some fit or engagement but is not ready for immediate sales follow-up."
    if status == "disqualified":
        return "Lead lacks enough verified fit, intent, or data quality for sales routing."
    return routing.get("review_reason") or "Lead needs human review before routing."


def _recommended_questions(missing_information: list[str]) -> list[str]:
    question_map = {
        "budget": "What budget range has been allocated for this initiative?",
        "timeline": "What implementation timeline are you targeting?",
        "current vendor": "What tools or vendors are you using for this workflow today?",
        "company domain": "Which company are you evaluating this for?",
        "company size": "How many employees or users would be involved?",
        "industry": "Which industry best describes your company?",
    }
    return [question_map[item] for item in missing_information if item in question_map]


def _data_sources_used(enrichment: JSONDict, behavior: JSONDict) -> list[str]:
    sources = ["raw_lead"]
    if enrichment:
        sources.append("provided_enrichment")
    if behavior:
        sources.append("provided_behavior")
    return sources


def build_lead_qualification_graph():
    """Build the LangGraph workflow for lead qualification."""
    graph = StateGraph(LeadQualificationState)
    graph.add_node("normalize", _normalize_node)
    graph.add_node("score", _score_node)
    graph.add_node("route", _route_node)
    graph.add_node("assemble", _assemble_node)
    graph.set_entry_point("normalize")
    graph.add_edge("normalize", "score")
    graph.add_edge("score", "route")
    graph.add_edge("route", "assemble")
    graph.add_edge("assemble", END)
    return graph.compile()


def qualify_lead(
    lead: JSONDict,
    behavior: JSONDict | None = None,
    enrichment: JSONDict | None = None,
) -> JSONDict:
    """Run the full lead qualification workflow and return structured output."""
    graph = build_lead_qualification_graph()
    state = graph.invoke(
        {
            "lead": lead,
            "behavior": behavior or {},
            "enrichment": enrichment or {},
        }
    )
    return state["final_output"]

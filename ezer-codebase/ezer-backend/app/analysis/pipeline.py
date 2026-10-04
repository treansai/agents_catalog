"""Pipeline d'analyse d'un message (anciennement un graphe LangGraph).

    judge_jev → decide_route(Jev) ─┬→ auto_file ──┐
        │                          ├→ surface ────┼→ assemble
        │                          └→ escalate ───┘
        └─erreur→ judge_rules ──────────────────────→ assemble

Sans client Jev, le pipeline commence directement par judge_rules. `decide_route` est le nœud de
décision : Jev choisit la destination. Seules deux gardes restent en code : un signal de sécurité
dur ou une confiance trop basse envoient à `escalate`. La politique de priorité et de relecture
reste dans `assemble_analysis`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis.assemble import assemble_analysis
from app.analysis.jev import JEV_PROVENANCE, JevClient, decide_route_with_jev, judge_with_jev
from app.analysis.signals import RULES_PROVENANCE, AnalysisProvenance, MessageSignals, Route, rule_signals
from app.domain.models import EmailAnalysis, MailMessage

# Sous cette confiance, la décision de Jev n'est pas suivie : un humain relit.
MIN_ROUTE_CONFIDENCE = 0.4


@dataclass
class PipelineState:
    message: MailMessage
    created_at: str
    signals: MessageSignals | None = None
    provenance: AnalysisProvenance | None = None
    # Nom de l'erreur Jev, jamais le message ni la réponse.
    jev_error: str | None = None
    analysis: EmailAnalysis | None = None


def _error_name(error: Exception) -> str:
    return type(error).__name__


async def judge_jev(client: JevClient, state: PipelineState) -> None:
    try:
        state.signals = await judge_with_jev(client, state.message)
        state.provenance = JEV_PROVENANCE
    except Exception as error:
        state.jev_error = _error_name(error)


async def decide_route(client: JevClient, state: PipelineState) -> None:
    if state.signals is None:
        raise RuntimeError("decide_route without judgments")
    try:
        route, confidence = await decide_route_with_jev(client, state.message, state.signals)
        state.signals = state.signals.with_route(route, confidence)
    except Exception as error:
        state.jev_error = _error_name(error)


def route_from_decision(state: PipelineState) -> str:
    signals = state.signals
    if signals is None or signals.route is None:
        return "judge_rules"
    if signals.prompt_injection or signals.phishing:
        return "escalate"
    if (signals.route_confidence or 0) < MIN_ROUTE_CONFIDENCE:
        return "escalate"
    return "escalate" if signals.route == "human_review" else signals.route


def judge_rules(state: PipelineState) -> None:
    state.signals = rule_signals(state.message)
    state.provenance = RULES_PROVENANCE


def assemble(state: PipelineState) -> None:
    if state.signals is None or state.provenance is None:
        raise RuntimeError("assemble reached without judgments")
    state.analysis = assemble_analysis(state.message, state.created_at, state.signals, state.provenance)


async def run_analysis(client: JevClient | None, message: MailMessage, created_at: str) -> PipelineState:
    state = PipelineState(message=message, created_at=created_at)
    if client is None:
        judge_rules(state)
    else:
        await judge_jev(client, state)
        if state.signals is None:
            judge_rules(state)
        else:
            await decide_route(client, state)
            destination = route_from_decision(state)
            if destination == "judge_rules":
                judge_rules(state)
            else:
                routed: Route = "human_review" if destination == "escalate" else destination  # type: ignore[assignment]
                state.signals = state.signals.with_route(routed, state.signals.route_confidence)
    assemble(state)
    return state

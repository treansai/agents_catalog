"""Assemblage de l'analyse finale : la politique de priorité et de relecture vit ici."""

from __future__ import annotations

import hashlib
import json
import re

from app.analysis.signals import AnalysisProvenance, MessageSignals
from app.domain.models import (
    ActionItem,
    EmailAnalysis,
    EmailCategory,
    MailMessage,
    Priority,
    RiskLevel,
    SafetyAssessment,
    TriageResult,
)
from app.services.timeutil import to_iso

# Sous ce seuil de confiance sur la catégorie, un humain relit le message.
UNCERTAIN_TRIAGE = 0.4

_WHITESPACE = re.compile(r"\s+")
# `\b` de JavaScript est limité à l'ASCII (« é » n'est pas un caractère de mot) : on le reproduit à
# l'identique, sans passer par `re.ASCII` qui dégraderait aussi la casse des lettres accentuées.
_BOUNDARY = r"(?:(?<![A-Za-z0-9_])(?=[A-Za-z0-9_])|(?<=[A-Za-z0-9_])(?![A-Za-z0-9_]))"
_FRENCH_SIGNALS = re.compile(rf"{_BOUNDARY}(bonjour|merci|avant|facture|réunion|équipe|votre|vous){_BOUNDARY}", re.I)
_ISO_DATE = re.compile(rf"{_BOUNDARY}(20[0-9]{{2}}-[0-9]{{2}}-[0-9]{{2}}){_BOUNDARY}")
_FRENCH_DATE = re.compile(
    rf"{_BOUNDARY}([0-9]{{1,2}}\s+(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre"
    rf"|octobre|novembre|décembre)(?:\s+20[0-9]{{2}})?){_BOUNDARY}",
    re.I,
)


def _truncate(value: str, maximum: int) -> str:
    return _WHITESPACE.sub(" ", value.strip())[:maximum]


def content_hash(message: MailMessage) -> str:
    canonical = json.dumps(
        {
            "body_text": message.body_text,
            "provider": message.provider,
            "provider_message_id": message.provider_message_id,
            "sender_address": message.sender_address,
            "subject": message.subject,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _detect_language(text: str) -> str:
    return "fr" if _FRENCH_SIGNALS.search(text) else "en"


def _due_date_from(text: str) -> str | None:
    iso = _ISO_DATE.search(text)
    if iso:
        return iso.group(1)
    french = _FRENCH_DATE.search(text)
    return french.group(1) if french else None


def assemble_analysis(
    message: MailMessage,
    created_at: str,
    signals: MessageSignals,
    provenance: AnalysisProvenance,
) -> EmailAnalysis:
    text = f"{message.subject}\n{message.snippet}\n{message.body_text}"
    lower = text.lower()
    prompt_injection = signals.prompt_injection
    phishing = signals.phishing
    spam = signals.spam
    urgent = signals.urgent
    action_required = signals.action_required

    category: EmailCategory = "informational"
    if prompt_injection or phishing:
        category = "security"
    elif signals.category is not None:
        category = signals.category
    elif spam:
        category = "spam"
    elif signals.receipt:
        category = "receipt"
    elif signals.newsletter:
        category = "newsletter"
    elif action_required:
        category = "action_required"

    priority: Priority = "normal"
    if category in ("spam", "newsletter"):
        priority = "low"
    if action_required:
        priority = "high"
    if signals.route == "auto_file" and not action_required:
        priority = "low"
    if signals.route == "surface" and priority == "low":
        priority = "normal"
    if (phishing and urgent) or prompt_injection:
        priority = "critical"

    risk_level: RiskLevel = "none"
    if phishing:
        risk_level = "high" if urgent else "medium"
    elif prompt_injection:
        risk_level = "high"
    elif spam:
        risk_level = "low"

    triage_confidence = signals.triage_confidence
    needs_human_review = (
        prompt_injection
        or phishing
        or priority == "critical"
        or signals.route == "human_review"
        or (1 if triage_confidence is None else triage_confidence) < UNCERTAIN_TRIAGE
    )
    summary_source = message.snippet or message.body_text or message.subject or "Message sans aperçu"
    summary = _truncate(summary_source, 360)
    indicators = [
        *(["instruction hostile détectée"] if prompt_injection else []),
        *(["demande sensible ou identité à vérifier"] if phishing else []),
        *(["langage d'urgence"] if urgent else []),
    ]
    sender_label = message.sender_name or message.sender_address
    key_points = [
        f"Objet : {_truncate(message.subject, 220)}" if message.subject else "Objet non renseigné",
        f"Expéditeur : {_truncate(sender_label, 220)}" if sender_label else "Expéditeur non renseigné",
        "Une réponse ou validation explicite est demandée." if action_required else "Aucune action explicite détectée.",
    ]
    action_items = (
        [
            ActionItem(
                description=_truncate(message.subject or summary, 500),
                owner=None,
                due_date=_due_date_from(lower),
                confidence=0.82,
            )
        ]
        if action_required
        else []
    )
    digest = content_hash(message)
    message_ref = f"{message.provider}:{message.account_id}:{message.provider_message_id}"
    analysis_id = hashlib.sha256(f"{message_ref}\u001f{digest}\u001f{provenance.pipeline_version}".encode()).hexdigest()
    if category == "security":
        rationale = "Le message contient un signal de sécurité qui exige de conserver la décision humaine."
    elif action_required:
        rationale = "Le texte formule une demande explicite ou une échéance."
    else:
        rationale = "Le message est informatif et ne contient pas de demande explicite."

    if signals.phishing_probability is not None:
        phishing_likelihood = signals.phishing_probability
    elif phishing:
        phishing_likelihood = 0.91 if urgent else 0.74
    else:
        phishing_likelihood = 0.22 if spam else 0.04
    if signals.safety_confidence is not None:
        safety_confidence = signals.safety_confidence
    else:
        safety_confidence = 0.9 if risk_level == "none" else 0.86

    return EmailAnalysis(
        analysis_id=analysis_id,
        message_ref=message_ref,
        content_hash=digest,
        pipeline_version=provenance.pipeline_version,
        model_id=provenance.model_id,
        prompt_version=provenance.prompt_version,
        created_at=to_iso(created_at),
        category=category,
        priority=priority,
        needs_human_review=needs_human_review,
        summary=summary,
        key_points=key_points,
        action_items=action_items,
        safety=SafetyAssessment(
            risk_level=risk_level,
            prompt_injection_detected=prompt_injection,
            phishing_likelihood=phishing_likelihood,
            indicators=indicators,
            rationale=(
                "Aucun signal hostile manifeste n'a été détecté par les règles locales."
                if risk_level == "none"
                else "Le contenu présente des signaux qui doivent être vérifiés avant toute action."
            ),
            confidence=safety_confidence,
        ),
        triage=TriageResult(
            category=category,
            priority=priority,
            needs_human_review=needs_human_review,
            confidence=0.84 if triage_confidence is None else triage_confidence,
            rationale=rationale,
        ),
        detected_language=_detect_language(text),
    )

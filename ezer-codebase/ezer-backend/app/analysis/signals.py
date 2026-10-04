"""Jugements sur un message, produits par les règles locales ou par Jev."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal

from app.domain.models import EmailCategory, MailMessage

PIPELINE_VERSION = "ezer-ts-v1"
MODEL_ID = "ezer-typescript-rules-v1"
PROMPT_VERSION = "heuristic-fr-v1"

ROUTES = ("auto_file", "surface", "human_review")
Route = Literal["auto_file", "surface", "human_review"]


@dataclass(frozen=True)
class MessageSignals:
    prompt_injection: bool
    phishing: bool
    spam: bool
    newsletter: bool
    receipt: bool
    action_required: bool
    urgent: bool
    # Catégorie choisie par le modèle ; sans elle, elle est déduite des indicateurs ci-dessus.
    category: EmailCategory | None = None
    # Probabilités et confiances mesurées ; sans elles, les valeurs fixes des règles s'appliquent.
    phishing_probability: float | None = None
    safety_confidence: float | None = None
    triage_confidence: float | None = None
    # Destination décidée par Jev dans le pipeline ; absente pour les règles locales.
    route: Route | None = None
    route_confidence: float | None = None

    def with_route(self, route: Route, confidence: float | None = None) -> MessageSignals:
        return replace(self, route=route, route_confidence=confidence)


@dataclass(frozen=True)
class AnalysisProvenance:
    pipeline_version: str
    model_id: str
    prompt_version: str


RULES_PROVENANCE = AnalysisProvenance(PIPELINE_VERSION, MODEL_ID, PROMPT_VERSION)

_PROMPT_INJECTION = re.compile(
    r"(ignore (all |the )?(previous|prior) instructions|system prompt|developer message|jailbreak)", re.I
)
_PHISHING = re.compile(
    r"(mot de passe|password|credential|identifiant|wire transfer|virement|urgent payment"
    r"|verify your account|connexion inhabituelle)",
    re.I,
)
_SPAM = re.compile(r"(winner|lottery|casino|free money|gagnant|désabonnez-vous immédiatement)", re.I)
_NEWSLETTER = re.compile(r"(newsletter|digest|édition de la semaine|unsubscribe|se désabonner)", re.I)
_RECEIPT = re.compile(r"(receipt|invoice|facture|reçu|payment confirmation|confirmation de paiement)", re.I)
_ACTION = re.compile(
    r"(merci de|please|action requise|required|avant |deadline|échéance|valider|approve|répondre|confirm)",
    re.I,
)
_URGENT = re.compile(r"(urgent|immédiat|immediate|critique|critical|aujourd'hui|today)", re.I)


def rule_signals(message: MailMessage) -> MessageSignals:
    text = f"{message.subject}\n{message.snippet}\n{message.body_text}"
    return MessageSignals(
        prompt_injection=_PROMPT_INJECTION.search(text) is not None,
        phishing=_PHISHING.search(text) is not None,
        spam=_SPAM.search(text) is not None,
        newsletter=_NEWSLETTER.search(text) is not None,
        receipt=_RECEIPT.search(text) is not None,
        action_required=_ACTION.search(text) is not None,
        urgent=_URGENT.search(text) is not None,
    )

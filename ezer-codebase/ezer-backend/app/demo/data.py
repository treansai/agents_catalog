"""Données de démonstration : deux comptes, sept messages, dont quatre déjà analysés."""

from __future__ import annotations

from typing import Any

from app.analysis.analyse_message import analyse_message
from app.domain.models import Account, MailMessage

DEMO_ACCOUNTS: list[Account] = [
    Account(id="gmail-primary", provider="gmail"),
    Account(id="outlook-ops", provider="outlook"),
]


def _message(
    account_id: str,
    provider: str,
    message_id: str,
    thread_id: str,
    subject: str,
    sender_name: str,
    sender_address: str,
    received_at: str,
    body_text: str,
    snippet: str,
) -> MailMessage:
    return MailMessage(
        account_id=account_id,
        provider=provider,  # type: ignore[arg-type]
        provider_message_id=message_id,
        thread_id=thread_id,
        subject=subject,
        sender_name=sender_name,
        sender_address=sender_address,
        received_at=received_at,
        body_text=body_text,
        snippet=snippet,
    )


DEMO_MESSAGES: list[MailMessage] = [
    _message(
        "gmail-primary",
        "gmail",
        "gmail-001",
        "thread-security",
        "Connexion inhabituelle à vérifier",
        "Équipe sécurité",
        "security@example.test",
        "2026-08-27T06:45:00.000Z",
        "Une connexion inhabituelle a été détectée. Vérifiez votre compte avant toute action.",
        "Une connexion inhabituelle demande votre attention.",
    ),
    _message(
        "gmail-primary",
        "gmail",
        "gmail-002",
        "thread-contract",
        "Validation du contrat avant le 29 août 2026",
        "Camille Martin",
        "camille@example.test",
        "2026-08-27T07:20:00.000Z",
        "Bonjour, merci de valider la dernière version du contrat avant le 29 août 2026.",
        "Merci de valider la dernière version du contrat.",
    ),
    _message(
        "gmail-primary",
        "gmail",
        "gmail-003",
        "thread-news",
        "Newsletter produit — édition de la semaine",
        "Atelier Produit",
        "hello@example.test",
        "2026-08-26T15:10:00.000Z",
        "Voici notre newsletter et les nouveautés de la semaine. Se désabonner depuis le site.",
        "Les nouveautés produit de cette semaine.",
    ),
    _message(
        "gmail-primary",
        "gmail",
        "gmail-004",
        "thread-receipt",
        "Reçu de paiement #8421",
        "Comptabilité",
        "billing@example.test",
        "2026-08-27T08:05:00.000Z",
        "Votre paiement a bien été reçu. Cette facture est disponible dans votre espace.",
        "Confirmation de paiement et facture disponible.",
    ),
    _message(
        "outlook-ops",
        "outlook",
        "outlook-001",
        "thread-incident",
        "Action requise — incident critique aujourd'hui",
        "Centre des opérations",
        "operations@example.test",
        "2026-08-27T07:50:00.000Z",
        "Incident critique en cours. Merci de confirmer le plan de reprise aujourd'hui.",
        "Confirmation urgente du plan de reprise.",
    ),
    _message(
        "outlook-ops",
        "outlook",
        "outlook-002",
        "thread-roadmap",
        "Compte rendu de la réunion roadmap",
        "Nora Bernard",
        "nora@example.test",
        "2026-08-27T08:30:00.000Z",
        "Bonjour, voici le compte rendu de notre réunion et les décisions prises par l'équipe.",
        "Décisions de la réunion roadmap.",
    ),
    _message(
        "outlook-ops",
        "outlook",
        "outlook-003",
        "thread-budget",
        "Merci de valider le budget avant le 30 août 2026",
        "Direction financière",
        "finance@example.test",
        "2026-08-27T09:00:00.000Z",
        "Merci de valider le budget révisé avant le 30 août 2026 pour clôturer le dossier.",
        "Validation du budget révisé attendue.",
    ),
]

SEEDED_MESSAGE_IDS = frozenset({"gmail-001", "gmail-002", "gmail-003", "outlook-001"})


def create_demo_state() -> dict[str, Any]:
    seeded = [m for m in DEMO_MESSAGES if m.provider_message_id in SEEDED_MESSAGE_IDS]
    analyses = [analyse_message(m, m.received_at).model_dump(mode="json") for m in seeded]
    analyses.sort(key=lambda item: item["created_at"], reverse=True)
    return {
        "schema_version": 1,
        "accounts": [account.to_json() for account in DEMO_ACCOUNTS],
        "analyses": analyses,
        "cursors": {"gmail-primary": "3", "outlook-ops": "1"},
    }

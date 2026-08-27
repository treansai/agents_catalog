import { analyseMessage } from "../analysis/analyse-message";
import type { Account, MailMessage, PersistedState } from "../domain/models";

export const DEMO_ACCOUNTS: Account[] = [
  { id: "gmail-primary", provider: "gmail" },
  { id: "outlook-ops", provider: "outlook" }
];

export const DEMO_MESSAGES: MailMessage[] = [
  {
    account_id: "gmail-primary",
    provider: "gmail",
    provider_message_id: "gmail-001",
    thread_id: "thread-security",
    subject: "Connexion inhabituelle à vérifier",
    sender_name: "Équipe sécurité",
    sender_address: "security@example.test",
    received_at: "2026-08-27T06:45:00.000Z",
    body_text: "Une connexion inhabituelle a été détectée. Vérifiez votre compte avant toute action.",
    snippet: "Une connexion inhabituelle demande votre attention."
  },
  {
    account_id: "gmail-primary",
    provider: "gmail",
    provider_message_id: "gmail-002",
    thread_id: "thread-contract",
    subject: "Validation du contrat avant le 29 août 2026",
    sender_name: "Camille Martin",
    sender_address: "camille@example.test",
    received_at: "2026-08-27T07:20:00.000Z",
    body_text: "Bonjour, merci de valider la dernière version du contrat avant le 29 août 2026.",
    snippet: "Merci de valider la dernière version du contrat."
  },
  {
    account_id: "gmail-primary",
    provider: "gmail",
    provider_message_id: "gmail-003",
    thread_id: "thread-news",
    subject: "Newsletter produit — édition de la semaine",
    sender_name: "Atelier Produit",
    sender_address: "hello@example.test",
    received_at: "2026-08-26T15:10:00.000Z",
    body_text: "Voici notre newsletter et les nouveautés de la semaine. Se désabonner depuis le site.",
    snippet: "Les nouveautés produit de cette semaine."
  },
  {
    account_id: "gmail-primary",
    provider: "gmail",
    provider_message_id: "gmail-004",
    thread_id: "thread-receipt",
    subject: "Reçu de paiement #8421",
    sender_name: "Comptabilité",
    sender_address: "billing@example.test",
    received_at: "2026-08-27T08:05:00.000Z",
    body_text: "Votre paiement a bien été reçu. Cette facture est disponible dans votre espace.",
    snippet: "Confirmation de paiement et facture disponible."
  },
  {
    account_id: "outlook-ops",
    provider: "outlook",
    provider_message_id: "outlook-001",
    thread_id: "thread-incident",
    subject: "Action requise — incident critique aujourd'hui",
    sender_name: "Centre des opérations",
    sender_address: "operations@example.test",
    received_at: "2026-08-27T07:50:00.000Z",
    body_text: "Incident critique en cours. Merci de confirmer le plan de reprise aujourd'hui.",
    snippet: "Confirmation urgente du plan de reprise."
  },
  {
    account_id: "outlook-ops",
    provider: "outlook",
    provider_message_id: "outlook-002",
    thread_id: "thread-roadmap",
    subject: "Compte rendu de la réunion roadmap",
    sender_name: "Nora Bernard",
    sender_address: "nora@example.test",
    received_at: "2026-08-27T08:30:00.000Z",
    body_text: "Bonjour, voici le compte rendu de notre réunion et les décisions prises par l'équipe.",
    snippet: "Décisions de la réunion roadmap."
  },
  {
    account_id: "outlook-ops",
    provider: "outlook",
    provider_message_id: "outlook-003",
    thread_id: "thread-budget",
    subject: "Merci de valider le budget avant le 30 août 2026",
    sender_name: "Direction financière",
    sender_address: "finance@example.test",
    received_at: "2026-08-27T09:00:00.000Z",
    body_text: "Merci de valider le budget révisé avant le 30 août 2026 pour clôturer le dossier.",
    snippet: "Validation du budget révisé attendue."
  }
];

const SEEDED_MESSAGE_IDS = new Set(["gmail-001", "gmail-002", "gmail-003", "outlook-001"]);

export function createDemoState(): PersistedState {
  const seededMessages = DEMO_MESSAGES.filter((message) => SEEDED_MESSAGE_IDS.has(message.provider_message_id));
  return {
    schema_version: 1,
    accounts: structuredClone(DEMO_ACCOUNTS),
    analyses: seededMessages
      .map((message) => analyseMessage(message, message.received_at))
      .sort((left, right) => right.created_at.localeCompare(left.created_at)),
    cursors: {
      "gmail-primary": "3",
      "outlook-ops": "1"
    }
  };
}

import { createHash } from "node:crypto";

import type {
  ActionItem,
  EmailAnalysis,
  EmailCategory,
  MailMessage,
  Priority,
  RiskLevel
} from "../domain/models";
import type { AnalysisProvenance, MessageSignals } from "./signals";

/** Sous ce seuil de confiance sur la catégorie, un humain relit le message. */
const UNCERTAIN_TRIAGE = 0.4;

function truncate(value: string, maximum: number): string {
  return value.trim().replace(/\s+/g, " ").slice(0, maximum);
}

function contentHash(message: MailMessage): string {
  const canonical = JSON.stringify({
    body_text: message.body_text,
    provider: message.provider,
    provider_message_id: message.provider_message_id,
    sender_address: message.sender_address,
    subject: message.subject
  });
  return createHash("sha256").update(canonical).digest("hex");
}

function detectLanguage(text: string): string {
  const frenchSignals = /\b(bonjour|merci|avant|facture|réunion|équipe|votre|vous)\b/i;
  return frenchSignals.test(text) ? "fr" : "en";
}

function dueDateFrom(text: string): string | null {
  const isoDate = text.match(/\b(20\d{2}-\d{2}-\d{2})\b/);
  if (isoDate?.[1]) return isoDate[1];
  const frenchDate = text.match(
    /\b(\d{1,2}\s+(?:janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)(?:\s+20\d{2})?)\b/i
  );
  return frenchDate?.[1] ?? null;
}

export function assembleAnalysis(
  message: MailMessage,
  createdAt: string,
  signals: MessageSignals,
  provenance: AnalysisProvenance
): EmailAnalysis {
  const text = `${message.subject}\n${message.snippet}\n${message.body_text}`;
  const lower = text.toLocaleLowerCase("fr");
  const { promptInjection, phishing, spam, newsletter, receipt, actionRequired, urgent } = signals;

  let category: EmailCategory = "informational";
  if (promptInjection || phishing) category = "security";
  else if (signals.category !== undefined) category = signals.category;
  else if (spam) category = "spam";
  else if (receipt) category = "receipt";
  else if (newsletter) category = "newsletter";
  else if (actionRequired) category = "action_required";

  let priority: Priority = "normal";
  if (category === "spam" || category === "newsletter") priority = "low";
  if (actionRequired) priority = "high";
  if (signals.route === "auto_file" && !actionRequired) priority = "low";
  if (signals.route === "surface" && priority === "low") priority = "normal";
  if ((phishing && urgent) || promptInjection) priority = "critical";

  let riskLevel: RiskLevel = "none";
  if (phishing) riskLevel = urgent ? "high" : "medium";
  else if (promptInjection) riskLevel = "high";
  else if (spam) riskLevel = "low";

  const needsHumanReview =
    promptInjection ||
    phishing ||
    priority === "critical" ||
    signals.route === "human_review" ||
    (signals.triageConfidence ?? 1) < UNCERTAIN_TRIAGE;
  const summarySource = message.snippet || message.body_text || message.subject || "Message sans aperçu";
  const summary = truncate(summarySource, 360);
  const indicators = [
    ...(promptInjection ? ["instruction hostile détectée"] : []),
    ...(phishing ? ["demande sensible ou identité à vérifier"] : []),
    ...(urgent ? ["langage d'urgence"] : [])
  ];
  const keyPoints = [
    message.subject ? `Objet : ${truncate(message.subject, 220)}` : "Objet non renseigné",
    message.sender_name || message.sender_address
      ? `Expéditeur : ${truncate(message.sender_name || message.sender_address, 220)}`
      : "Expéditeur non renseigné",
    actionRequired ? "Une réponse ou validation explicite est demandée." : "Aucune action explicite détectée."
  ];
  const actionItems: ActionItem[] = actionRequired
    ? [
        {
          description: truncate(message.subject || summary, 500),
          owner: null,
          due_date: dueDateFrom(lower),
          confidence: 0.82
        }
      ]
    : [];
  const hash = contentHash(message);
  const messageRef = `${message.provider}:${message.account_id}:${message.provider_message_id}`;
  const analysisId = createHash("sha256")
    .update(`${messageRef}\u001f${hash}\u001f${provenance.pipelineVersion}`)
    .digest("hex");
  const rationale =
    category === "security"
      ? "Le message contient un signal de sécurité qui exige de conserver la décision humaine."
      : actionRequired
        ? "Le texte formule une demande explicite ou une échéance."
        : "Le message est informatif et ne contient pas de demande explicite.";

  return {
    analysis_id: analysisId,
    message_ref: messageRef,
    content_hash: hash,
    pipeline_version: provenance.pipelineVersion,
    model_id: provenance.modelId,
    prompt_version: provenance.promptVersion,
    created_at: new Date(createdAt).toISOString(),
    category,
    priority,
    needs_human_review: needsHumanReview,
    summary,
    key_points: keyPoints,
    action_items: actionItems,
    safety: {
      risk_level: riskLevel,
      prompt_injection_detected: promptInjection,
      phishing_likelihood:
        signals.phishingProbability ?? (phishing ? (urgent ? 0.91 : 0.74) : spam ? 0.22 : 0.04),
      indicators,
      rationale:
        riskLevel === "none"
          ? "Aucun signal hostile manifeste n'a été détecté par les règles locales."
          : "Le contenu présente des signaux qui doivent être vérifiés avant toute action.",
      confidence: signals.safetyConfidence ?? (riskLevel === "none" ? 0.9 : 0.86)
    },
    triage: {
      category,
      priority,
      needs_human_review: needsHumanReview,
      confidence: signals.triageConfidence ?? 0.84,
      rationale
    },
    detected_language: detectLanguage(text)
  };
}

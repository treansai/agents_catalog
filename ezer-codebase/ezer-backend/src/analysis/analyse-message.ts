import { createHash } from "node:crypto";

import type {
  ActionItem,
  EmailAnalysis,
  EmailCategory,
  MailMessage,
  Priority,
  RiskLevel
} from "../domain/models";

export const PIPELINE_VERSION = "ezer-ts-v1";
export const MODEL_ID = "ezer-typescript-rules-v1";
export const PROMPT_VERSION = "heuristic-fr-v1";

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

export function analyseMessage(message: MailMessage, createdAt = new Date().toISOString()): EmailAnalysis {
  const text = `${message.subject}\n${message.snippet}\n${message.body_text}`;
  const lower = text.toLocaleLowerCase("fr");
  const promptInjection = /(ignore (all |the )?(previous|prior) instructions|system prompt|developer message|jailbreak)/i.test(
    text
  );
  const phishing = /(mot de passe|password|credential|identifiant|wire transfer|virement|urgent payment|verify your account|connexion inhabituelle)/i.test(
    text
  );
  const spam = /(winner|lottery|casino|free money|gagnant|désabonnez-vous immédiatement)/i.test(text);
  const newsletter = /(newsletter|digest|édition de la semaine|unsubscribe|se désabonner)/i.test(text);
  const receipt = /(receipt|invoice|facture|reçu|payment confirmation|confirmation de paiement)/i.test(text);
  const actionRequired = /(merci de|please|action requise|required|avant |deadline|échéance|valider|approve|répondre|confirm)/i.test(
    text
  );
  const urgent = /(urgent|immédiat|immediate|critique|critical|aujourd'hui|today)/i.test(text);

  let category: EmailCategory = "informational";
  if (promptInjection || phishing) category = "security";
  else if (spam) category = "spam";
  else if (receipt) category = "receipt";
  else if (newsletter) category = "newsletter";
  else if (actionRequired) category = "action_required";

  let priority: Priority = "normal";
  if (category === "spam" || category === "newsletter") priority = "low";
  if (actionRequired) priority = "high";
  if ((phishing && urgent) || promptInjection) priority = "critical";

  let riskLevel: RiskLevel = "none";
  if (phishing) riskLevel = urgent ? "high" : "medium";
  else if (promptInjection) riskLevel = "high";
  else if (spam) riskLevel = "low";

  const needsHumanReview = promptInjection || phishing || priority === "critical";
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
    .update(`${messageRef}\u001f${hash}\u001f${PIPELINE_VERSION}`)
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
    pipeline_version: PIPELINE_VERSION,
    model_id: MODEL_ID,
    prompt_version: PROMPT_VERSION,
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
      phishing_likelihood: phishing ? (urgent ? 0.91 : 0.74) : spam ? 0.22 : 0.04,
      indicators,
      rationale:
        riskLevel === "none"
          ? "Aucun signal hostile manifeste n'a été détecté par les règles locales."
          : "Le contenu présente des signaux qui doivent être vérifiés avant toute action.",
      confidence: riskLevel === "none" ? 0.9 : 0.86
    },
    triage: {
      category,
      priority,
      needs_human_review: needsHumanReview,
      confidence: 0.84,
      rationale
    },
    detected_language: detectLanguage(text)
  };
}

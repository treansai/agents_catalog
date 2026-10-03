import type { EmailCategory, MailMessage } from "../domain/models";

export const PIPELINE_VERSION = "ezer-ts-v1";
export const MODEL_ID = "ezer-typescript-rules-v1";
export const PROMPT_VERSION = "heuristic-fr-v1";

/** Jugements sur un message ; produits par les règles locales ou par Jev. */
export const ROUTES = ["auto_file", "surface", "human_review"] as const;
export type Route = (typeof ROUTES)[number];

export interface MessageSignals {
  promptInjection: boolean;
  phishing: boolean;
  spam: boolean;
  newsletter: boolean;
  receipt: boolean;
  actionRequired: boolean;
  urgent: boolean;
  /** Catégorie choisie par le modèle ; sans elle, elle est déduite des indicateurs ci-dessus. */
  category?: EmailCategory;
  /** Probabilités et confiances mesurées ; sans elles, les valeurs fixes des règles s'appliquent. */
  phishingProbability?: number;
  safetyConfidence?: number;
  triageConfidence?: number;
  /** Destination décidée par Jev dans le graphe ; absente pour les règles locales. */
  route?: Route;
  routeConfidence?: number;
}

export interface AnalysisProvenance {
  pipelineVersion: string;
  modelId: string;
  promptVersion: string;
}

export const RULES_PROVENANCE: AnalysisProvenance = {
  pipelineVersion: PIPELINE_VERSION,
  modelId: MODEL_ID,
  promptVersion: PROMPT_VERSION
};

export function ruleSignals(message: MailMessage): MessageSignals {
  const text = `${message.subject}\n${message.snippet}\n${message.body_text}`;
  return {
    promptInjection: /(ignore (all |the )?(previous|prior) instructions|system prompt|developer message|jailbreak)/i.test(
      text
    ),
    phishing: /(mot de passe|password|credential|identifiant|wire transfer|virement|urgent payment|verify your account|connexion inhabituelle)/i.test(
      text
    ),
    spam: /(winner|lottery|casino|free money|gagnant|désabonnez-vous immédiatement)/i.test(text),
    newsletter: /(newsletter|digest|édition de la semaine|unsubscribe|se désabonner)/i.test(text),
    receipt: /(receipt|invoice|facture|reçu|payment confirmation|confirmation de paiement)/i.test(text),
    actionRequired: /(merci de|please|action requise|required|avant |deadline|échéance|valider|approve|répondre|confirm)/i.test(
      text
    ),
    urgent: /(urgent|immédiat|immediate|critique|critical|aujourd'hui|today)/i.test(text)
  };
}

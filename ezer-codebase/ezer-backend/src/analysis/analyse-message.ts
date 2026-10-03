import type { EmailAnalysis, MailMessage } from "../domain/models";
import { assembleAnalysis } from "./assemble";
import { RULES_PROVENANCE, ruleSignals } from "./signals";

export { MODEL_ID, PIPELINE_VERSION, PROMPT_VERSION } from "./signals";

/** Analyse déterministe par règles locales : graine de démonstration et repli sans Jev. */
export function analyseMessage(message: MailMessage, createdAt = new Date().toISOString()): EmailAnalysis {
  return assembleAnalysis(message, createdAt, ruleSignals(message), RULES_PROVENANCE);
}

import { Annotation, END, START, StateGraph } from "@langchain/langgraph";
import type { TypeSafeClient } from "@typesafe-ai/sdk";

import type { EmailAnalysis, MailMessage } from "../domain/models";
import { assembleAnalysis } from "./assemble";
import { decideRouteWithJev, JEV_PROVENANCE, judgeWithJev } from "./jev";
import { RULES_PROVENANCE, ruleSignals, type AnalysisProvenance, type MessageSignals } from "./signals";

const AnalysisState = Annotation.Root({
  message: Annotation<MailMessage>(),
  createdAt: Annotation<string>(),
  signals: Annotation<MessageSignals | undefined>(),
  provenance: Annotation<AnalysisProvenance | undefined>(),
  /** Nom de l'erreur Jev, jamais le message ni la réponse. */
  jevError: Annotation<string | undefined>(),
  analysis: Annotation<EmailAnalysis | undefined>()
});

type State = typeof AnalysisState.State;

/** Sous cette confiance, la décision de Jev n'est pas suivie : un humain relit. */
const MIN_ROUTE_CONFIDENCE = 0.4;

/**
 * Graphe d'analyse d'un message :
 *   START → judge_jev → decide_route(Jev) ─┬→ auto_file ──┐
 *               │                          ├→ surface ────┼→ assemble → END
 *               │                          └→ escalate ───┘
 *               └─erreur→ judge_rules ──────────────────────→ assemble
 * Sans client Jev, le graphe commence directement par judge_rules.
 * `decide_route` est le nœud de décision : Jev choisit l'arête sortante. Seules deux gardes
 * restent en code : un signal de sécurité dur ou une confiance trop basse envoient à `escalate`.
 * La politique de priorité et de relecture reste dans `assembleAnalysis`.
 */
export function buildAnalysisGraph(client: TypeSafeClient | undefined) {
  return new StateGraph(AnalysisState)
    .addNode("judge_jev", async (state: State) => {
      if (client === undefined) throw new Error("judge_jev reached without a client");
      try {
        return { signals: await judgeWithJev(client, state.message), provenance: JEV_PROVENANCE };
      } catch (error) {
        return { jevError: error instanceof Error ? error.name : "unknown" };
      }
    })
    .addNode("decide_route", async (state: State) => {
      if (client === undefined || state.signals === undefined) throw new Error("decide_route without judgments");
      try {
        const { route, confidence } = await decideRouteWithJev(client, state.message, state.signals);
        return { signals: { ...state.signals, route, routeConfidence: confidence } };
      } catch (error) {
        return { jevError: error instanceof Error ? error.name : "unknown" };
      }
    })
    .addNode("auto_file", (state: State) => ({ signals: { ...state.signals!, route: "auto_file" as const } }))
    .addNode("surface", (state: State) => ({ signals: { ...state.signals!, route: "surface" as const } }))
    .addNode("escalate", (state: State) => ({ signals: { ...state.signals!, route: "human_review" as const } }))
    .addNode("judge_rules", (state: State) => ({
      signals: ruleSignals(state.message),
      provenance: RULES_PROVENANCE
    }))
    .addNode("assemble", (state: State) => {
      if (state.signals === undefined || state.provenance === undefined) {
        throw new Error("assemble reached without judgments");
      }
      return { analysis: assembleAnalysis(state.message, state.createdAt, state.signals, state.provenance) };
    })
    .addConditionalEdges(START, () => (client === undefined ? "judge_rules" : "judge_jev"), [
      "judge_jev",
      "judge_rules"
    ])
    .addConditionalEdges(
      "judge_jev",
      (state: State) => (state.signals === undefined ? "judge_rules" : "decide_route"),
      ["judge_rules", "decide_route"]
    )
    .addConditionalEdges("decide_route", routeFromDecision, ["auto_file", "surface", "escalate", "judge_rules"])
    .addEdge("auto_file", "assemble")
    .addEdge("surface", "assemble")
    .addEdge("escalate", "assemble")
    .addEdge("judge_rules", "assemble")
    .addEdge("assemble", END)
    .compile();
}

function routeFromDecision(state: State): "auto_file" | "surface" | "escalate" | "judge_rules" {
  const signals = state.signals;
  if (signals?.route === undefined) return "judge_rules";
  if (signals.promptInjection || signals.phishing) return "escalate";
  if ((signals.routeConfidence ?? 0) < MIN_ROUTE_CONFIDENCE) return "escalate";
  return signals.route === "human_review" ? "escalate" : signals.route;
}

export type AnalysisGraph = ReturnType<typeof buildAnalysisGraph>;

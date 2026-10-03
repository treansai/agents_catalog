import { Injectable, Logger } from "@nestjs/common";
import { TypeSafeClient } from "@typesafe-ai/sdk";

import type { EmailAnalysis, MailMessage } from "../domain/models";
import { buildAnalysisGraph, type AnalysisGraph } from "./analysis.graph";

const REQUEST_TIMEOUT_MS = 10_000;

/** Façade Nest du graphe d'analyse (LangGraph.js) : Jev si TYPESAFE_API_KEY est définie, sinon règles. */
@Injectable()
export class MessageAnalyzer {
  private readonly logger = new Logger(MessageAnalyzer.name);
  private readonly graph: AnalysisGraph;

  constructor() {
    const key = process.env.TYPESAFE_API_KEY?.trim();
    this.graph = buildAnalysisGraph(key ? new TypeSafeClient({ apiKey: key, timeout: REQUEST_TIMEOUT_MS }) : undefined);
  }

  async analyse(message: MailMessage, createdAt = new Date().toISOString()): Promise<EmailAnalysis> {
    const result = await this.graph.invoke({ message, createdAt });
    if (result.jevError !== undefined) {
      this.logger.warn(`Jev unavailable, using local rules (${result.jevError})`);
    }
    if (result.analysis === undefined) throw new Error("analysis graph produced no result");
    return result.analysis;
  }
}

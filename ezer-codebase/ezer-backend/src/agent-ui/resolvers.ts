import { Injectable } from "@nestjs/common";

import { ConnectionError } from "../mail/outlook-auth.service";
import { GraphMailService, type MessageHeader } from "../mail/graph-mail.service";
import { JsonPersistenceService } from "../persistence/json-persistence.service";
import type { Account, EmailAnalysis } from "../domain/models";
import { AppConfigService } from "../config/app-config.service";
import { ResolverCache } from "./cache";
import { assertSchema, SchemaValidationError, type JsonSchema } from "./schema";
import { MAIL_PAGE_SCHEMA, type DataResolverContext } from "./contracts";
import { agentUiError, AgentUiError } from "./errors";
import { emitAgentUiEvent } from "./telemetry";
import {
  geocode,
  route as routeBetween,
  straightLine,
  travelMode,
  type MapProviders,
  type Place,
  type Route,
  type TravelMode
} from "./places";

const RESOLVER_TIMEOUT_MS = 12_000;
const MAX_PAGE = 25;

export interface DataResolverDefinition {
  id: string;
  inputSchema: JsonSchema;
  outputSchema: JsonSchema;
  requiredPermissions: string[];
  cacheTtlMs: number;
  execute: (input: Record<string, unknown>, context: DataResolverContext) => Promise<unknown>;
}

const PAGE_INPUT: JsonSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    query: { type: "string", maxLength: 200 },
    limit: { type: "integer", minimum: 1, maximum: 25 },
    offset: { type: "integer", minimum: 0, maximum: 1_000_000 },
    unread_only: { type: "boolean" }
  }
};

const PLACE_SCHEMA: JsonSchema = {
  type: "object",
  additionalProperties: false,
  required: ["name", "lon", "lat"],
  properties: {
    name: { type: "string", minLength: 1, maxLength: 200 },
    lon: { type: "number", minimum: -180, maximum: 180 },
    lat: { type: "number", minimum: -90, maximum: 90 }
  }
};

const ROUTE_SCHEMA: JsonSchema = {
  type: "object",
  additionalProperties: false,
  required: ["origin", "destination", "mode", "distanceKm", "durationMin", "coordinates", "estimated"],
  properties: {
    origin: PLACE_SCHEMA,
    destination: PLACE_SCHEMA,
    mode: { type: "string", enum: ["driving", "walking", "cycling"] },
    distanceKm: { type: "number", minimum: 0, maximum: 40_000 },
    durationMin: { type: "integer", minimum: 0, maximum: 100_000 },
    estimated: { type: "boolean" },
    coordinates: {
      type: "array",
      minItems: 2,
      maxItems: 500,
      items: {
        type: "array",
        minItems: 2,
        maxItems: 2,
        items: { type: "number", minimum: -180, maximum: 180 }
      }
    }
  }
};

/**
 * Lieux de repli, utilisés sans clé de géocodage. Le jeu est volontairement court : il sert la
 * démonstration, pas la production.
 */
const KNOWN_PLACES: { match: RegExp; place: Place }[] = [
  { match: /le rival/i, place: { name: "Le Rival, 1 Rue Rambuteau, 75004 Paris", lon: 2.353_2, lat: 48.860_6 } },
  { match: /ch[aâ]telet|h[oô]tel de ville/i, place: { name: "Châtelet, Paris", lon: 2.347_3, lat: 48.858_2 } },
  { match: /gare de lyon/i, place: { name: "Gare de Lyon, Paris", lon: 2.373_6, lat: 48.844_3 } },
  { match: /gare du nord/i, place: { name: "Gare du Nord, Paris", lon: 2.355_4, lat: 48.880_9 } },
  { match: /montmartre|sacr[ée].?c[oœ]ur/i, place: { name: "Montmartre, Paris", lon: 2.343_1, lat: 48.886_7 } },
  { match: /la d[ée]fense/i, place: { name: "La Défense, Puteaux", lon: 2.238_0, lat: 48.891_9 } },
  { match: /paris/i, place: { name: "Paris, France", lon: 2.352_2, lat: 48.856_6 } }
];

function knownPlace(query: string): Place | undefined {
  return KNOWN_PLACES.find((entry) => entry.match.test(query))?.place;
}

function routeToOutput(computed: Route): Record<string, unknown> {
  return {
    origin: { ...computed.origin },
    destination: { ...computed.destination },
    mode: computed.mode,
    distanceKm: computed.distanceKm,
    durationMin: computed.durationMin,
    coordinates: computed.coordinates
  };
}

function pageBounds(input: Record<string, unknown>): { limit: number; offset: number } {
  const limit = typeof input.limit === "number" ? input.limit : 20;
  const offset = typeof input.offset === "number" ? input.offset : 0;
  return {
    limit: Math.min(Math.max(limit, 1), MAX_PAGE),
    offset: Math.min(Math.max(offset, 0), 1_000_000)
  };
}

function analysisToRow(analysis: EmailAnalysis): Record<string, unknown> {
  const parts = analysis.message_ref.split(":");
  return {
    id: parts[2] ?? analysis.analysis_id,
    sender: analysis.summary.slice(0, 80),
    senderAddress: "",
    subject: analysis.summary.slice(0, 400),
    snippet: analysis.key_points[0] ?? analysis.summary.slice(0, 200),
    receivedAt: analysis.created_at,
    unread: analysis.needs_human_review,
    hasAttachments: false,
    tag: analysis.category
  };
}

function headerToRow(header: MessageHeader, tag?: string): Record<string, unknown> {
  return {
    id: header.message_id,
    sender: header.sender_name || header.sender_address,
    senderAddress: header.sender_address,
    snippet: header.snippet,
    subject: header.subject,
    receivedAt: header.received_at,
    unread: header.is_read === false,
    hasAttachments: header.has_attachments,
    ...(tag !== undefined ? { tag } : {})
  };
}

function looksLikeInvoice(analysis: EmailAnalysis): boolean {
  if (analysis.category === "receipt") return true;
  const haystack = `${analysis.summary} ${analysis.key_points.join(" ")}`.toLowerCase();
  return /facture|invoice|reçu|paiement|billing/.test(haystack);
}

@Injectable()
export class DataResolverRegistry {
  private readonly resolvers: Map<string, DataResolverDefinition>;
  private readonly inFlight = new Map<string, Promise<unknown>>();

  constructor(
    private readonly persistence: JsonPersistenceService,
    private readonly graph: GraphMailService,
    private readonly config: AppConfigService,
    private readonly cache: ResolverCache
  ) {
    this.resolvers = new Map(
      [
        this.messagesSearch(),
        this.invoicesSearch(),
        this.messagesGet(),
        this.mailboxStats(),
        this.metricsReceipts(),
        this.sendersTally(),
        this.analysesTriage(),
        this.placesRoute()
      ].map((resolver) => [resolver.id, resolver])
    );
  }

  get(resolverId: string): DataResolverDefinition | undefined {
    return this.resolvers.get(resolverId);
  }

  async execute(
    resolverId: string,
    input: Record<string, unknown>,
    context: DataResolverContext,
    componentId: string
  ): Promise<unknown> {
    const resolver = this.resolvers.get(resolverId);
    if (resolver === undefined) throw agentUiError("unknown_resolver", 400);
    if (!resolver.requiredPermissions.every((permission) => context.permissions.includes(permission))) {
      emitAgentUiEvent({
        event: "data_resolver_failed",
        traceId: context.traceId,
        resolverId,
        componentId,
        status: "denied",
        code: "permission_denied"
      });
      throw agentUiError("permission_denied", 403);
    }
    try {
      assertSchema(input, resolver.inputSchema);
    } catch (error) {
      if (error instanceof SchemaValidationError) throw agentUiError("invalid_payload", 400);
      throw error;
    }
    const cacheKey = `${context.workspaceId}:${resolverId}:${JSON.stringify(input)}`;
    const cached = this.cache.get(cacheKey, context.workspaceId);
    if (cached !== undefined) return cached;

    const started = Date.now();
    emitAgentUiEvent({
      event: "data_resolver_started",
      traceId: context.traceId,
      resolverId,
      componentId,
      instanceId: context.traceId,
      status: "ok"
    });

    const pending = this.inFlight.get(cacheKey);
    if (pending !== undefined) return pending;

    const run = this.withTimeout(() => resolver.execute(input, context), context.signal);
    this.inFlight.set(cacheKey, run);
    try {
      const output = await run;
      assertSchema(output, resolver.outputSchema);
      this.cache.set(cacheKey, context.workspaceId, output, resolver.cacheTtlMs);
      emitAgentUiEvent({
        event: "data_resolver_succeeded",
        traceId: context.traceId,
        resolverId,
        componentId,
        durationMs: Date.now() - started,
        status: "ok"
      });
      return output;
    } catch (error) {
      if (error instanceof AgentUiError) throw error;
      if (error instanceof SchemaValidationError) throw agentUiError("resolver_failed", 500);
      emitAgentUiEvent({
        event: "data_resolver_failed",
        traceId: context.traceId,
        resolverId,
        componentId,
        durationMs: Date.now() - started,
        status: "error",
        code: error instanceof Error ? error.name : "resolver_failed"
      });
      if (error instanceof ConnectionError) throw agentUiError("resolver_failed", 422);
      throw error;
    } finally {
      this.inFlight.delete(cacheKey);
    }
  }

  private withTimeout<T>(operation: () => Promise<T>, signal?: AbortSignal): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), RESOLVER_TIMEOUT_MS);
    const onAbort = () => controller.abort();
    signal?.addEventListener("abort", onAbort);
    return Promise.race([
      operation(),
      new Promise<T>((_, reject) => {
        controller.signal.addEventListener("abort", () => reject(agentUiError("timeout", 504)));
      })
    ]).finally(() => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    });
  }

  private async account(workspaceId: string): Promise<Account> {
    const accounts = await this.persistence.listAccounts();
    const account = accounts.find((candidate) => candidate.id === workspaceId);
    if (account === undefined) throw agentUiError("workspace_mismatch", 403);
    return account;
  }

  private async scopedAnalyses(workspaceId: string): Promise<EmailAnalysis[]> {
    const page = await this.persistence.listAnalyses(100, 0, { accountId: workspaceId });
    return page.items;
  }


  /**
   * Un nom de lieu, éventuellement un point de départ, et un itinéraire tracé.
   *
   * Les fournisseurs viennent de l'environnement ; l'agent n'envoie que du texte. Sans clé, ou en
   * mode démonstration, on retombe sur des lieux connus et un segment direct, marqué comme
   * estimation dans la sortie.
   */
  private placesRoute(): DataResolverDefinition {
    return {
      id: "places.route",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        required: ["to"],
        properties: {
          to: { type: "string", minLength: 1, maxLength: 200 },
          from: { type: "string", minLength: 1, maxLength: 200 },
          mode: { type: "string", enum: ["driving", "walking", "cycling"] }
        }
      },
      outputSchema: ROUTE_SCHEMA,
      requiredPermissions: ["mail.read"],
      cacheTtlMs: 60_000,
      execute: async (input) => {
        const mode = travelMode(input.mode);
        const to = String(input.to).slice(0, 200);
        const from = typeof input.from === "string" ? input.from.slice(0, 200) : this.config.defaultOrigin;
        const providers: MapProviders = {
          maptilerKey: this.config.maptilerKey,
          routingUrl: this.config.routingUrl,
          nominatimUrl: this.config.nominatimUrl,
          contact: this.config.mapContact
        };
        const [origin, destination] = await Promise.all([
          this.locate(from, providers),
          this.locate(to, providers)
        ]);
        return this.trace(origin, destination, mode, providers);
      }
    };
  }

  private async locate(query: string, providers: MapProviders): Promise<Place> {
    const known = knownPlace(query);
    try {
      const found = await geocode(query, providers);
      if (found !== undefined) return found;
    } catch {
      // Fournisseur injoignable : on retombe sur les lieux connus plutôt que d'échouer sèchement.
    }
    if (known === undefined) throw agentUiError("resolver_failed", 422, "place_not_found");
    return known;
  }

  private async trace(
    origin: Place,
    destination: Place,
    mode: TravelMode,
    providers: MapProviders
  ): Promise<Record<string, unknown>> {
    try {
      const computed = await routeBetween(origin, destination, mode, providers);
      if (computed !== undefined) return { ...routeToOutput(computed), estimated: false };
    } catch {
      // Service de routage indisponible : l'estimation vaut mieux qu'une carte vide.
    }
    return { ...routeToOutput(straightLine(origin, destination, mode)), estimated: true };
  }

  private messagesSearch(): DataResolverDefinition {
    return {
      id: "messages.search",
      inputSchema: PAGE_INPUT,
      outputSchema: MAIL_PAGE_SCHEMA,
      requiredPermissions: ["mail.read"],
      cacheTtlMs: 15_000,
      execute: async (input, context) => {
        const { limit, offset } = pageBounds(input);
        const query = typeof input.query === "string" ? input.query : undefined;
        const account = await this.account(context.workspaceId);
        try {
          const headers =
            query === undefined
              ? await this.graph.listMessages(account, {
                  top: Math.min(limit + offset, MAX_PAGE),
                  unreadOnly: input.unread_only === true
                })
              : await this.graph.search(account, query, Math.min(limit + offset, MAX_PAGE));
          const sliced = headers.slice(offset, offset + limit);
          return {
            items: sliced.map((header) => headerToRow(header)),
            total: headers.length,
            limit,
            offset
          };
        } catch (error) {
          if (!(error instanceof ConnectionError) && this.config.mode !== "demo") throw error;
          return this.pageFromAnalyses(context.workspaceId, limit, offset, (analysis) => {
            if (query === undefined) return true;
            const haystack = `${analysis.summary} ${analysis.key_points.join(" ")}`.toLowerCase();
            return haystack.includes(query.toLowerCase());
          });
        }
      }
    };
  }

  private invoicesSearch(): DataResolverDefinition {
    return {
      id: "invoices.search",
      inputSchema: PAGE_INPUT,
      outputSchema: MAIL_PAGE_SCHEMA,
      requiredPermissions: ["mail.read"],
      cacheTtlMs: 15_000,
      execute: async (input, context) => {
        const { limit, offset } = pageBounds(input);
        const query = typeof input.query === "string" ? input.query : "facture";
        const account = await this.account(context.workspaceId);
        try {
          const headers = await this.graph.search(
            account,
            query,
            Math.min(limit + offset, MAX_PAGE)
          );
          const sliced = headers.slice(offset, offset + limit);
          return {
            items: sliced.map((header) => headerToRow(header, "finances")),
            total: headers.length,
            limit,
            offset
          };
        } catch (error) {
          if (!(error instanceof ConnectionError) && this.config.mode !== "demo") throw error;
          return this.pageFromAnalyses(context.workspaceId, limit, offset, (analysis) => {
            if (!looksLikeInvoice(analysis)) return false;
            if (query.trim() === "") return true;
            const haystack = `${analysis.summary} ${analysis.key_points.join(" ")}`.toLowerCase();
            return haystack.includes(query.toLowerCase()) || looksLikeInvoice(analysis);
          });
        }
      }
    };
  }

  private messagesGet(): DataResolverDefinition {
    return {
      id: "messages.get",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        required: ["message_id"],
        properties: { message_id: { type: "string", minLength: 1, maxLength: 512 } }
      },
      outputSchema: {
        type: "object",
        additionalProperties: false,
        required: ["id", "subject", "sender", "receivedAt", "body"],
        properties: {
          id: { type: "string", maxLength: 512 },
          subject: { type: "string", maxLength: 400 },
          sender: { type: "string", maxLength: 320 },
          senderAddress: { type: "string", maxLength: 320 },
          receivedAt: { type: "string", maxLength: 64 },
          body: { type: "string", maxLength: 20_000 }
        }
      },
      requiredPermissions: ["mail.read"],
      cacheTtlMs: 30_000,
      execute: async (input, context) => {
        const messageId = String(input.message_id);
        const account = await this.account(context.workspaceId);
        try {
          const message = await this.graph.getMessage(account, messageId);
          return {
            id: message.message_id,
            subject: message.subject,
            sender: message.sender_name || message.sender_address,
            senderAddress: message.sender_address,
            receivedAt: message.received_at,
            body: message.body_text
          };
        } catch (error) {
          if (!(error instanceof ConnectionError) && this.config.mode !== "demo") throw error;
          const analyses = await this.scopedAnalyses(context.workspaceId);
          const match = analyses.find((analysis) => {
            const id = analysis.message_ref.split(":")[2];
            return id === messageId || analysis.analysis_id === messageId;
          });
          if (match === undefined) throw agentUiError("resolver_failed", 404);
          return {
            id: messageId,
            subject: match.summary.slice(0, 400),
            sender: "expéditeur",
            senderAddress: "",
            receivedAt: match.created_at,
            body: [match.summary, ...match.key_points].join("\n")
          };
        }
      }
    };
  }

  private mailboxStats(): DataResolverDefinition {
    return {
      id: "mailbox.stats",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      outputSchema: {
        type: "object",
        additionalProperties: false,
        required: ["value", "label"],
        properties: {
          value: { type: "string", maxLength: 32 },
          label: { type: "string", maxLength: 80 },
          hint: { type: "string", maxLength: 160 },
          series: { type: "array", maxItems: 31, items: { type: "number" } },
          title: { type: "string", maxLength: 80 },
          body: { type: "string", maxLength: 600 },
          when: { type: "string", maxLength: 40 }
        }
      },
      requiredPermissions: ["mail.read"],
      cacheTtlMs: 20_000,
      execute: async (_input, context) => {
        const account = await this.account(context.workspaceId);
        try {
          const stats = await this.graph.stats(account);
          return {
            value: String(stats.unread_messages),
            label: "non lus",
            hint: `${stats.total_messages} messages`,
            title: stats.mailbox,
            body: `${stats.unread_messages} non lus sur ${stats.total_messages} messages.`,
            when: new Date().toISOString().slice(0, 10)
          };
        } catch (error) {
          if (!(error instanceof ConnectionError) && this.config.mode !== "demo") throw error;
          const analyses = await this.scopedAnalyses(context.workspaceId);
          return {
            value: String(analyses.length),
            label: "analyses",
            hint: "volume dans ce workspace",
            title: account.mailbox ?? account.id,
            body: `${analyses.length} analyses disponibles dans cette boîte.`,
            when: new Date().toISOString().slice(0, 10)
          };
        }
      }
    };
  }

  private metricsReceipts(): DataResolverDefinition {
    return {
      id: "metrics.receipts",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      outputSchema: {
        type: "object",
        additionalProperties: false,
        required: ["value", "label"],
        properties: {
          value: { type: "string", maxLength: 32 },
          label: { type: "string", maxLength: 80 },
          hint: { type: "string", maxLength: 160 },
          series: { type: "array", maxItems: 31, items: { type: "number" } }
        }
      },
      requiredPermissions: ["mail.read"],
      cacheTtlMs: 20_000,
      execute: async (_input, context) => {
        const month = new Date().toISOString().slice(0, 7);
        const analyses = (await this.scopedAnalyses(context.workspaceId)).filter(
          (analysis) => looksLikeInvoice(analysis) && analysis.created_at.startsWith(month)
        );
        return {
          value: String(analyses.length),
          label: "reçus ce mois",
          hint: "volume de factures et reçus détectés, pas un montant extrait",
          series: analyses.map(() => 1)
        };
      }
    };
  }

  private sendersTally(): DataResolverDefinition {
    return {
      id: "senders.tally",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        properties: { sample: { type: "integer", minimum: 1, maximum: 25 } }
      },
      outputSchema: {
        type: "object",
        additionalProperties: false,
        required: ["items"],
        properties: {
          items: {
            type: "array",
            maxItems: 25,
            items: {
              type: "object",
              additionalProperties: false,
              required: ["sender", "senderAddress", "total", "unread"],
              properties: {
                sender: { type: "string", maxLength: 320 },
                senderAddress: { type: "string", maxLength: 320 },
                total: { type: "integer", minimum: 0 },
                unread: { type: "integer", minimum: 0 }
              }
            }
          }
        }
      },
      requiredPermissions: ["mail.read"],
      cacheTtlMs: 20_000,
      execute: async (input, context) => {
        const sample = typeof input.sample === "number" ? input.sample : 25;
        const account = await this.account(context.workspaceId);
        try {
          const senders = await this.graph.talliesBySender(account, sample);
          return {
            items: senders.map((sender) => ({
              sender: sender.sender_name || sender.sender_address,
              senderAddress: sender.sender_address,
              total: sender.total,
              unread: sender.unread
            }))
          };
        } catch (error) {
          if (!(error instanceof ConnectionError) && this.config.mode !== "demo") throw error;
          return { items: [] };
        }
      }
    };
  }

  private analysesTriage(): DataResolverDefinition {
    return {
      id: "analyses.triage",
      inputSchema: PAGE_INPUT,
      outputSchema: MAIL_PAGE_SCHEMA,
      requiredPermissions: ["mail.read"],
      cacheTtlMs: 15_000,
      execute: async (input, context) => {
        const { limit, offset } = pageBounds(input);
        return this.pageFromAnalyses(context.workspaceId, limit, offset, () => true);
      }
    };
  }

  private async pageFromAnalyses(
    workspaceId: string,
    limit: number,
    offset: number,
    predicate: (analysis: EmailAnalysis) => boolean
  ): Promise<{ items: Record<string, unknown>[]; total: number; limit: number; offset: number }> {
    const matching = (await this.scopedAnalyses(workspaceId)).filter(predicate);
    return {
      items: matching.slice(offset, offset + limit).map(analysisToRow),
      total: matching.length,
      limit,
      offset
    };
  }
}

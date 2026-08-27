import type { ComponentType } from "react";

import type { JsonSchema } from "@/lib/agent-ui/schema";
import type { AgentComponentProps } from "@/components/library/agent-props";
import { AgentUiClientError } from "@/lib/agent-ui/contracts";

export interface AgentComponentDefinition {
  id: string;
  version: string;
  title: string;
  description: string;
  capabilities: string[];
  useWhen: string[];
  avoidWhen?: string[];
  propsSchema: JsonSchema;
  dataSchema?: JsonSchema;
  allowedDataResolvers: string[];
  allowedActions: string[];
  examples: Record<string, unknown>[];
  load: () => Promise<{ default: ComponentType<AgentComponentProps> }>;
}

const MAIL_LIST_PROPS: JsonSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    title: { type: "string", maxLength: 120 },
    pageSize: { type: "integer", minimum: 1, maximum: 25 },
  },
};

const MAIL_PAGE: JsonSchema = {
  type: "object",
  additionalProperties: false,
  required: ["items", "total", "limit", "offset"],
  properties: {
    items: {
      type: "array",
      maxItems: 25,
      items: {
        type: "object",
        additionalProperties: false,
        required: ["id", "sender", "subject", "snippet", "receivedAt", "unread"],
        properties: {
          id: { type: "string", maxLength: 512 },
          sender: { type: "string", maxLength: 320 },
          senderAddress: { type: "string", maxLength: 320 },
          subject: { type: "string", maxLength: 400 },
          snippet: { type: "string", maxLength: 600 },
          receivedAt: { type: "string", maxLength: 64 },
          unread: { type: "boolean" },
          hasAttachments: { type: "boolean" },
          tag: { type: "string", maxLength: 32 },
        },
      },
    },
    total: { type: "integer", minimum: 0, maximum: 1_000_000 },
    limit: { type: "integer", minimum: 1, maximum: 25 },
    offset: { type: "integer", minimum: 0, maximum: 1_000_000 },
  },
};

const ROUTE_PLACE: JsonSchema = {
  type: "object",
  additionalProperties: false,
  required: ["name", "lon", "lat"],
  properties: {
    name: { type: "string", maxLength: 200 },
    lon: { type: "number", minimum: -180, maximum: 180 },
    lat: { type: "number", minimum: -90, maximum: 90 },
  },
};

const ROUTE_DATA: JsonSchema = {
  type: "object",
  additionalProperties: false,
  required: ["origin", "destination", "mode", "distanceKm", "durationMin", "coordinates"],
  properties: {
    origin: ROUTE_PLACE,
    destination: ROUTE_PLACE,
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
        items: { type: "number", minimum: -180, maximum: 180 },
      },
    },
  },
};

/**
 * Table statique : le componentId de l'agent ne peut jamais devenir un chemin d'import.
 */
export const AGENT_COMPONENT_REGISTRY: AgentComponentDefinition[] = [
  {
    id: "mail.list",
    version: "1.0",
    title: "Liste de messages",
    description: "Tableau paginé de messages ou de factures issues de la boîte.",
    capabilities: ["display_table"],
    useWhen: ["derniers messages, factures ou reçus", "résultat de recherche explorable"],
    propsSchema: MAIL_LIST_PROPS,
    dataSchema: MAIL_PAGE,
    allowedDataResolvers: ["invoices.search", "messages.search", "analyses.triage"],
    allowedActions: ["messages.open", "invoices.open", "table.page", "messages.trash"],
    examples: [
      {
        componentId: "mail.list",
        data: { mode: "resolver", resolverId: "invoices.search", input: { limit: 20, query: "facture" } },
        fallbackText: "Voici vos dernières factures.",
      },
    ],
    load: () => import("@/components/library/mail").then((module) => ({ default: module.MailListAgent })),
  },
  {
    id: "mail.detail",
    version: "1.0",
    title: "Message ouvert",
    description: "Affiche un message identifié.",
    capabilities: ["display_document"],
    useWhen: ["ouvrir un message précis"],
    propsSchema: { type: "object", additionalProperties: false, properties: { title: { type: "string", maxLength: 120 } } },
    dataSchema: {
      type: "object",
      additionalProperties: false,
      required: ["id", "subject", "sender", "receivedAt", "body"],
      properties: {
        id: { type: "string", maxLength: 512 },
        subject: { type: "string", maxLength: 400 },
        sender: { type: "string", maxLength: 320 },
        senderAddress: { type: "string", maxLength: 320 },
        receivedAt: { type: "string", maxLength: 64 },
        body: { type: "string", maxLength: 20_000 },
      },
    },
    allowedDataResolvers: ["messages.get"],
    allowedActions: ["messages.trash", "draft.reply"],
    examples: [],
    load: () => import("@/components/library/mail").then((module) => ({ default: module.MailDetailAgent })),
  },
  {
    id: "metric.card",
    version: "1.0",
    title: "Carte métrique",
    description: "Une valeur chiffrée sans graphique superflu.",
    capabilities: ["display_metrics"],
    useWhen: ["une seule grandeur répond à la question"],
    propsSchema: {
      type: "object",
      additionalProperties: false,
      properties: { label: { type: "string", maxLength: 80 }, hint: { type: "string", maxLength: 160 } },
    },
    dataSchema: {
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
        when: { type: "string", maxLength: 40 },
      },
    },
    allowedDataResolvers: ["mailbox.stats", "metrics.receipts"],
    allowedActions: [],
    examples: [
      {
        componentId: "metric.card",
        data: { mode: "resolver", resolverId: "metrics.receipts", input: {} },
        fallbackText: "Voici le volume de reçus de ce mois.",
      },
    ],
    load: () => import("@/components/library/metrics").then((module) => ({ default: module.MetricCardAgent })),
  },
  {
    id: "senders.list",
    version: "1.0",
    title: "Expéditeurs",
    description: "Classement des expéditeurs les plus actifs.",
    capabilities: ["display_table"],
    useWhen: ["qui m'écrit le plus"],
    propsSchema: MAIL_LIST_PROPS,
    dataSchema: {
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
              unread: { type: "integer", minimum: 0 },
            },
          },
        },
      },
    },
    allowedDataResolvers: ["senders.tally"],
    allowedActions: ["messages.open"],
    examples: [],
    load: () => import("@/components/library/metrics").then((module) => ({ default: module.SendersListAgent })),
  },
  {
    id: "map.route",
    version: "1.0",
    title: "Carte et trajet",
    description:
      "Carte interactive montrant un lieu et le trajet pour s'y rendre : restaurant, gare, adresse.",
    capabilities: ["display_map", "display_route"],
    useWhen: ["itinéraire ou trajet vers un lieu", "situer un restaurant ou une adresse"],
    avoidWhen: ["une simple adresse en texte suffit"],
    propsSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        title: { type: "string", maxLength: 120 },
        note: { type: "string", maxLength: 200 },
      },
    },
    dataSchema: ROUTE_DATA,
    allowedDataResolvers: ["places.route"],
    allowedActions: [],
    examples: [
      {
        componentId: "map.route",
        data: {
          mode: "resolver",
          resolverId: "places.route",
          input: { to: "Le Rival, Paris", mode: "walking" },
        },
        fallbackText: "Je n'ai pas pu afficher le trajet.",
      },
    ],
    load: () => import("@/components/library/map").then((module) => ({ default: module.RouteMapAgent })),
  },
  {
    id: "confirm.dialog",
    version: "1.0",
    title: "Confirmation",
    description: "Second clic avant une mutation sensible.",
    capabilities: ["request_confirmation"],
    useWhen: ["suppression, envoi, publication"],
    propsSchema: {
      type: "object",
      additionalProperties: false,
      required: ["title", "body", "confirmLabel", "reversible"],
      properties: {
        title: { type: "string", maxLength: 160 },
        body: { type: "string", maxLength: 600 },
        confirmLabel: { type: "string", maxLength: 40 },
        cancelLabel: { type: "string", maxLength: 40 },
        reversible: { type: "boolean" },
        targetLabel: { type: "string", maxLength: 200 },
      },
    },
    dataSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        confirmationId: { type: "string", maxLength: 128 },
        token: { type: "string", maxLength: 128 },
        action: { type: "string", maxLength: 80 },
        target: { type: "string", maxLength: 400 },
        impact: { type: "string", maxLength: 400 },
        reversible: { type: "boolean" },
      },
    },
    allowedDataResolvers: [],
    allowedActions: ["confirmation.confirm", "confirmation.cancel"],
    examples: [],
    load: () => import("@/components/library/feedback").then((module) => ({ default: module.ConfirmAgent })),
  },
  {
    id: "empty.state",
    version: "1.0",
    title: "État vide",
    description: "Aucun élément à afficher.",
    capabilities: ["display_empty_state"],
    useWhen: ["liste vide"],
    propsSchema: {
      type: "object",
      additionalProperties: false,
      required: ["title"],
      properties: { title: { type: "string", maxLength: 80 }, detail: { type: "string", maxLength: 200 } },
    },
    allowedDataResolvers: [],
    allowedActions: [],
    examples: [],
    load: () => import("@/components/library/feedback").then((module) => ({ default: module.EmptyStateAgent })),
  },
  {
    id: "action.feed",
    version: "1.0",
    title: "Journal d'actions",
    description: "États d'un travail en cours.",
    capabilities: ["display_status"],
    useWhen: ["montrer ce que l'assistant vient de faire"],
    propsSchema: { type: "object", additionalProperties: false, properties: {} },
    dataSchema: {
      type: "object",
      additionalProperties: false,
      required: ["items"],
      properties: {
        items: {
          type: "array",
          maxItems: 24,
          items: {
            type: "object",
            additionalProperties: false,
            required: ["text", "meta"],
            properties: {
              text: { type: "string", maxLength: 200 },
              meta: { type: "string", maxLength: 80 },
              live: { type: "boolean" },
            },
          },
        },
      },
    },
    allowedDataResolvers: [],
    allowedActions: [],
    examples: [],
    load: () => import("@/components/library/feedback").then((module) => ({ default: module.ActionFeedAgent })),
  },
  {
    id: "morning.brief",
    version: "1.0",
    title: "Résumé du matin",
    description: "Digest court de la boîte.",
    capabilities: ["display_metrics", "display_status"],
    useWhen: ["résume ma matinée"],
    propsSchema: {
      type: "object",
      additionalProperties: false,
      properties: { title: { type: "string", maxLength: 80 } },
    },
    dataSchema: {
      type: "object",
      additionalProperties: false,
      required: ["title", "body", "when"],
      properties: {
        title: { type: "string", maxLength: 80 },
        body: { type: "string", maxLength: 600 },
        when: { type: "string", maxLength: 40 },
        value: { type: "string", maxLength: 32 },
        label: { type: "string", maxLength: 80 },
        hint: { type: "string", maxLength: 160 },
        series: { type: "array", maxItems: 31, items: { type: "number" } },
      },
    },
    allowedDataResolvers: ["mailbox.stats"],
    allowedActions: [],
    examples: [],
    load: () => import("@/components/library/metrics").then((module) => ({ default: module.MorningBriefAgent })),
  },
  {
    id: "choices.chips",
    version: "1.0",
    title: "Choix",
    description: "Filtres cliquables.",
    capabilities: ["display_choices"],
    useWhen: ["proposer un filtre"],
    propsSchema: {
      type: "object",
      additionalProperties: false,
      required: ["options"],
      properties: {
        options: {
          type: "array",
          minItems: 1,
          maxItems: 12,
          items: {
            type: "object",
            additionalProperties: false,
            required: ["id", "label"],
            properties: {
              id: { type: "string", maxLength: 64 },
              label: { type: "string", maxLength: 40 },
              selected: { type: "boolean" },
            },
          },
        },
      },
    },
    allowedDataResolvers: [],
    allowedActions: ["table.filter"],
    examples: [],
    load: () => import("@/components/library/feedback").then((module) => ({ default: module.ChoicesAgent })),
  },
];

const BY_ID = new Map(AGENT_COMPONENT_REGISTRY.map((entry) => [entry.id, entry]));

export function getRegisteredComponent(componentId: string): AgentComponentDefinition | undefined {
  return BY_ID.get(componentId);
}

export function requireRegisteredComponent(componentId: string, version: string): AgentComponentDefinition {
  const definition = BY_ID.get(componentId);
  if (definition === undefined) throw new AgentUiClientError("unknown_component", componentId);
  if (definition.version !== version) throw new AgentUiClientError("version_mismatch", version);
  return definition;
}

export function toPublicCatalog(): Array<Omit<AgentComponentDefinition, "load">> {
  return AGENT_COMPONENT_REGISTRY.map((entry) => ({
    id: entry.id,
    version: entry.version,
    title: entry.title,
    description: entry.description,
    capabilities: entry.capabilities,
    useWhen: entry.useWhen,
    avoidWhen: entry.avoidWhen,
    propsSchema: entry.propsSchema,
    dataSchema: entry.dataSchema,
    allowedDataResolvers: entry.allowedDataResolvers,
    allowedActions: entry.allowedActions,
    examples: entry.examples,
  }));
}

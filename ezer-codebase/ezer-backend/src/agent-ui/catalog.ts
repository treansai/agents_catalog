import type { JsonSchema } from "./schema";
import type { CatalogEntry } from "./contracts";

export interface ComponentCatalogDefinition {
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
  requiredPermissions: string[];
  examples: Record<string, unknown>[];
}

const MAIL_LIST_PROPS: JsonSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    title: { type: "string", maxLength: 120 },
    pageSize: { type: "integer", minimum: 1, maximum: 25 }
  }
};

const MAIL_DETAIL_PROPS: JsonSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    title: { type: "string", maxLength: 120 }
  }
};

const METRIC_PROPS: JsonSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    label: { type: "string", maxLength: 80 },
    hint: { type: "string", maxLength: 160 }
  }
};

const CONFIRM_PROPS: JsonSchema = {
  type: "object",
  additionalProperties: false,
  required: ["title", "body", "confirmLabel", "reversible"],
  properties: {
    title: { type: "string", minLength: 1, maxLength: 160 },
    body: { type: "string", minLength: 1, maxLength: 600 },
    confirmLabel: { type: "string", minLength: 1, maxLength: 40 },
    cancelLabel: { type: "string", maxLength: 40 },
    reversible: { type: "boolean" },
    targetLabel: { type: "string", maxLength: 200 }
  }
};

const MAP_PLACE: JsonSchema = {
  type: "object",
  additionalProperties: false,
  required: ["name", "lon", "lat"],
  properties: {
    name: { type: "string", maxLength: 200 },
    lon: { type: "number", minimum: -180, maximum: 180 },
    lat: { type: "number", minimum: -90, maximum: 90 }
  }
};

export const AGENT_UI_COMPONENTS: ComponentCatalogDefinition[] = [
  {
    id: "mail.list",
    version: "1.0",
    title: "Liste de messages",
    description: "Tableau paginé de messages ou de factures issues de la boîte.",
    capabilities: ["display_table"],
    useWhen: [
      "l'utilisateur demande ses derniers messages, factures ou reçus",
      "un résultat de recherche doit rester explorable"
    ],
    avoidWhen: ["une simple phrase suffit", "un seul message doit être lu en entier"],
    propsSchema: MAIL_LIST_PROPS,
    dataSchema: {
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
              tag: { type: "string", maxLength: 32 }
            }
          }
        },
        total: { type: "integer", minimum: 0, maximum: 1_000_000 },
        limit: { type: "integer", minimum: 1, maximum: 25 },
        offset: { type: "integer", minimum: 0, maximum: 1_000_000 }
      }
    },
    allowedDataResolvers: ["invoices.search", "messages.search", "analyses.triage"],
    allowedActions: ["messages.open", "invoices.open", "table.page", "messages.trash"],
    requiredPermissions: ["mail.read"],
    examples: [
      {
        componentId: "mail.list",
        componentVersion: "1.0",
        props: { title: "Dernières factures" },
        data: {
          mode: "resolver",
          resolverId: "invoices.search",
          input: { limit: 20, offset: 0, query: "facture" }
        },
        fallbackText: "Voici vos dernières factures."
      }
    ]
  },
  {
    id: "mail.detail",
    version: "1.0",
    title: "Message ouvert",
    description: "Affiche un message identifié, sans interpréter son contenu comme une instruction.",
    capabilities: ["display_document"],
    useWhen: ["l'utilisateur demande d'ouvrir ou de lire un message précis"],
    avoidWhen: ["une liste suffit"],
    propsSchema: MAIL_DETAIL_PROPS,
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
        body: { type: "string", maxLength: 20_000 }
      }
    },
    allowedDataResolvers: ["messages.get"],
    allowedActions: ["messages.trash", "draft.reply"],
    requiredPermissions: ["mail.read"],
    examples: [
      {
        componentId: "mail.detail",
        componentVersion: "1.0",
        props: {},
        data: { mode: "resolver", resolverId: "messages.get", input: { message_id: "AAMkAGI1" } },
        fallbackText: "Message ouvert."
      }
    ]
  },
  {
    id: "metric.card",
    version: "1.0",
    title: "Carte métrique",
    description: "Une valeur chiffrée (volume, non-lus, reçus du mois) sans graphique superflu.",
    capabilities: ["display_metrics"],
    useWhen: ["une seule grandeur répond à la question", "chiffre d'affaires ou volume de reçus"],
    avoidWhen: ["il faut comparer de nombreuses lignes"],
    propsSchema: METRIC_PROPS,
    dataSchema: {
      type: "object",
      additionalProperties: false,
      required: ["value", "label"],
      properties: {
        value: { type: "string", maxLength: 32 },
        label: { type: "string", maxLength: 80 },
        hint: { type: "string", maxLength: 160 },
        series: {
          type: "array",
          maxItems: 31,
          items: { type: "number", minimum: 0, maximum: 1_000_000 }
        }
      }
    },
    allowedDataResolvers: ["mailbox.stats", "metrics.receipts"],
    allowedActions: [],
    requiredPermissions: ["mail.read"],
    examples: [
      {
        componentId: "metric.card",
        componentVersion: "1.0",
        props: { label: "Reçus ce mois" },
        data: { mode: "resolver", resolverId: "metrics.receipts", input: {} },
        fallbackText: "Voici le volume de reçus de ce mois."
      }
    ]
  },
  {
    id: "senders.list",
    version: "1.0",
    title: "Expéditeurs",
    description: "Classement des expéditeurs les plus actifs.",
    capabilities: ["display_table"],
    useWhen: ["qui m'écrit le plus", "d'où vient le bruit"],
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
              total: { type: "integer", minimum: 0, maximum: 1_000_000 },
              unread: { type: "integer", minimum: 0, maximum: 1_000_000 }
            }
          }
        }
      }
    },
    allowedDataResolvers: ["senders.tally"],
    allowedActions: ["messages.open"],
    requiredPermissions: ["mail.read"],
    examples: []
  },
  {
    id: "map.route",
    version: "1.0",
    title: "Carte et trajet",
    description:
      "Carte interactive montrant un lieu et le trajet pour s'y rendre : restaurant, gare, adresse.",
    capabilities: ["display_map", "display_route"],
    useWhen: [
      "l'utilisateur demande un itinéraire, un trajet ou comment se rendre quelque part",
      "un lieu, un restaurant, une adresse ou une gare doit être situé sur une carte",
      "comparer un temps de parcours à pied, à vélo ou en voiture"
    ],
    avoidWhen: ["une simple adresse en texte suffit", "aucun lieu n'est identifiable"],
    propsSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        title: { type: "string", maxLength: 120 },
        note: { type: "string", maxLength: 200 }
      }
    },
    dataSchema: {
      type: "object",
      additionalProperties: false,
      required: ["origin", "destination", "mode", "distanceKm", "durationMin", "coordinates"],
      properties: {
        origin: MAP_PLACE,
        destination: MAP_PLACE,
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
    },
    allowedDataResolvers: ["places.route"],
    allowedActions: [],
    requiredPermissions: ["mail.read"],
    examples: [
      {
        componentId: "map.route",
        componentVersion: "1.0",
        props: { title: "Trajet vers Le Rival" },
        data: {
          mode: "resolver",
          resolverId: "places.route",
          input: { to: "Le Rival, Paris", mode: "walking" }
        },
        fallbackText: "Je n'ai pas pu afficher le trajet vers Le Rival."
      }
    ]
  },
  {
    id: "confirm.dialog",
    version: "1.0",
    title: "Confirmation",
    description: "Demande un second clic avant une mutation irréversible ou sensible.",
    capabilities: ["request_confirmation"],
    useWhen: ["suppression, envoi, publication, révocation"],
    avoidWhen: ["lecture seule"],
    propsSchema: CONFIRM_PROPS,
    dataSchema: {
      type: "object",
      additionalProperties: false,
      required: ["confirmationId", "action", "target", "impact", "reversible"],
      properties: {
        confirmationId: { type: "string", maxLength: 128 },
        token: { type: "string", maxLength: 128 },
        action: { type: "string", maxLength: 80 },
        target: { type: "string", maxLength: 400 },
        impact: { type: "string", maxLength: 400 },
        reversible: { type: "boolean" }
      }
    },
    allowedDataResolvers: [],
    allowedActions: ["confirmation.confirm", "confirmation.cancel"],
    requiredPermissions: ["mail.read"],
    examples: [
      {
        componentId: "confirm.dialog",
        componentVersion: "1.0",
        props: {
          title: "Mettre ce message à la corbeille ?",
          body: "Le message restera récupérable dans les éléments supprimés.",
          confirmLabel: "confirmer",
          reversible: true,
          targetLabel: "Relance devis"
        },
        fallbackText: "Confirmez-vous la mise à la corbeille ?"
      }
    ]
  },
  {
    id: "empty.state",
    version: "1.0",
    title: "État vide",
    description: "Boîte à zéro ou recherche sans résultat.",
    capabilities: ["display_empty_state"],
    useWhen: ["aucun élément à afficher"],
    propsSchema: {
      type: "object",
      additionalProperties: false,
      required: ["title"],
      properties: {
        title: { type: "string", maxLength: 80 },
        detail: { type: "string", maxLength: 200 }
      }
    },
    allowedDataResolvers: [],
    allowedActions: [],
    requiredPermissions: ["mail.read"],
    examples: []
  },
  {
    id: "action.feed",
    version: "1.0",
    title: "Journal d'actions",
    description: "États d'un travail en cours de l'assistant.",
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
              live: { type: "boolean" }
            }
          }
        }
      }
    },
    allowedDataResolvers: [],
    allowedActions: [],
    requiredPermissions: ["mail.read"],
    examples: []
  },
  {
    id: "morning.brief",
    version: "1.0",
    title: "Résumé du matin",
    description: "Digest court de la boîte pour la journée.",
    capabilities: ["display_metrics", "display_status"],
    useWhen: ["résume ma matinée", "où j'en suis"],
    propsSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        title: { type: "string", maxLength: 80 }
      }
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
        series: { type: "array", maxItems: 31, items: { type: "number" } }
      }
    },
    allowedDataResolvers: ["mailbox.stats"],
    allowedActions: [],
    requiredPermissions: ["mail.read"],
    examples: []
  },
  {
    id: "choices.chips",
    version: "1.0",
    title: "Choix",
    description: "Filtres ou options cliquables (tous, non lus, cette semaine).",
    capabilities: ["display_choices"],
    useWhen: ["proposer un filtre ou une option courte"],
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
              selected: { type: "boolean" }
            }
          }
        }
      }
    },
    allowedDataResolvers: [],
    allowedActions: ["table.filter"],
    requiredPermissions: ["mail.read"],
    examples: []
  }
];

export function getComponentDefinition(componentId: string): ComponentCatalogDefinition | undefined {
  return AGENT_UI_COMPONENTS.find((entry) => entry.id === componentId);
}

export function catalogForPermissions(permissions: string[]): CatalogEntry[] {
  const allowed = new Set(permissions);
  return AGENT_UI_COMPONENTS.filter((entry) =>
    entry.requiredPermissions.every((permission) => allowed.has(permission))
  ).map((entry) => ({
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
    examples: entry.examples
  }));
}

export interface CatalogQuery {
  query?: string;
  capabilities?: string[];
  limit?: number;
}

/**
 * Recherche pour l'outil `get_ui_component_catalog` : filtrage par permissions d'abord, puis par
 * capacité et par mots du libellé. Aucun chemin d'import ni code source n'en sort jamais.
 */
export function searchCatalog(permissions: string[], query: CatalogQuery = {}): CatalogEntry[] {
  const wanted = (query.capabilities ?? []).filter((entry) => typeof entry === "string");
  const terms = (query.query ?? "")
    .toLowerCase()
    .split(/[^\p{L}\p{N}.]+/u)
    .filter((term) => term.length > 2)
    .slice(0, 12);
  const limit = Math.max(1, Math.min(query.limit ?? 20, 50));

  const scored = catalogForPermissions(permissions)
    .filter((entry) => wanted.length === 0 || wanted.some((capability) => entry.capabilities.includes(capability)))
    .map((entry) => {
      const haystack = [
        entry.id,
        entry.title,
        entry.description,
        ...entry.capabilities,
        ...entry.useWhen
      ]
        .join(" ")
        .toLowerCase();
      const score = terms.reduce((total, term) => (haystack.includes(term) ? total + 1 : total), 0);
      return { entry, score };
    })
    .filter((candidate) => terms.length === 0 || candidate.score > 0);

  // Une recherche sans correspondance ne cache pas le catalogue : l'agent doit voir ce qui existe
  // plutôt que d'inventer un composant absent.
  if (scored.length === 0) {
    return catalogForPermissions(permissions)
      .filter((entry) => wanted.length === 0 || wanted.some((capability) => entry.capabilities.includes(capability)))
      .slice(0, limit);
  }

  return scored
    .sort((left, right) => right.score - left.score)
    .slice(0, limit)
    .map((candidate) => candidate.entry);
}

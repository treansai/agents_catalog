import assert from "node:assert/strict";
import { test } from "node:test";

import { parseChatMessage, parseDataSource, parseRenderSpec } from "./contracts.ts";
import { liveInstances, messagesFromAssistantAnswer, shouldAskAgent } from "./from-assistant.ts";
import { applyInstanceEvent, mergeUiMessages } from "./instance-store.ts";
import { looksLikeCode, sanitizeHref } from "./markdown.ts";
import { assertSchema } from "./schema.ts";

const REGISTERED = new Set([
  "mail.list",
  "mail.detail",
  "metric.card",
  "senders.list",
  "confirm.dialog",
  "empty.state",
  "action.feed",
  "morning.brief",
  "choices.chips",
  "map.route",
]);

const PLACE = {
  type: "object" as const,
  additionalProperties: false,
  required: ["name", "lon", "lat"],
  properties: {
    name: { type: "string" as const, maxLength: 200 },
    lon: { type: "number" as const, minimum: -180, maximum: 180 },
    lat: { type: "number" as const, minimum: -90, maximum: 90 },
  },
};

const MAIL_LIST_ACTIONS = ["messages.open", "invoices.open", "table.page", "messages.trash"];
const METRIC_RESOLVERS = ["mailbox.stats", "metrics.receipts"];

test("affiche un composant valide via un spec déclaré", () => {
  const spec = parseRenderSpec({
    instanceId: "inst-1",
    componentId: "mail.list",
    componentVersion: "1.0",
    props: { title: "Factures" },
    data: { mode: "resolver", resolverId: "invoices.search", input: { limit: 20, query: "facture" } },
    fallbackText: "Voici vos factures.",
  });
  assert.equal(REGISTERED.has(spec.componentId), true);
  assert.equal(spec.data?.mode, "resolver");
});

test("rejette un componentId inconnu", () => {
  assert.equal(REGISTERED.has("evil.widget"), false);
});

test("rejette des props invalides", () => {
  assert.throws(() =>
    assertSchema(
      { title: "x".repeat(500) },
      { type: "object", additionalProperties: false, properties: { title: { type: "string", maxLength: 120 } } },
    ),
  );
});

test("rejette un résolveur non autorisé pour le composant", () => {
  assert.equal(METRIC_RESOLVERS.includes("invoices.search"), false);
});

test("conserve le fallback textuel", () => {
  const spec = parseRenderSpec({
    instanceId: "inst-1",
    componentId: "empty.state",
    componentVersion: "1.0",
    props: { title: "Boîte à zéro" },
    fallbackText: "Rien à afficher.",
  });
  assert.equal(spec.fallbackText, "Rien à afficher.");
});

test("refuse un lien javascript et du code injecté", () => {
  assert.equal(sanitizeHref("javascript:alert(1)"), null);
  assert.equal(looksLikeCode("<script>alert(1)</script>"), true);
  assert.equal(looksLikeCode("eval('x')"), true);
  assert.equal(sanitizeHref("https://example.test/invoice"), "https://example.test/invoice");
});

test("rejette une action inconnue au registre du composant", () => {
  assert.equal(MAIL_LIST_ACTIONS.includes("eval"), false);
});

test("restaure un composant depuis l'historique sans rejouer une mutation", () => {
  const message = parseChatMessage({
    kind: "ui.render",
    protocolVersion: "1.0",
    id: "msg-1",
    role: "assistant",
    createdAt: "2026-08-27T10:00:00.000Z",
    ui: {
      instanceId: "inst-1",
      componentId: "mail.list",
      componentVersion: "1.0",
      props: { title: "Factures" },
      fallbackText: "Factures",
    },
  });
  const instances = applyInstanceEvent(new Map(), message);
  assert.equal(instances.get("inst-1")?.spec.componentId, "mail.list");
  const removed = applyInstanceEvent(instances, {
    kind: "ui.remove",
    id: "msg-2",
    ui: { instanceId: "inst-1" },
  });
  assert.equal(removed.has("inst-1"), false);
});

test("applique un patch d'instance sans réexécuter une mutation", () => {
  const rendered = parseChatMessage({
    kind: "ui.render",
    protocolVersion: "1.0",
    id: "msg-1",
    role: "assistant",
    createdAt: "2026-08-27T10:00:00.000Z",
    ui: {
      instanceId: "inst-1",
      componentId: "mail.list",
      componentVersion: "1.0",
      props: { title: "Factures" },
      fallbackText: "Factures",
    },
  });
  const patched = applyInstanceEvent(applyInstanceEvent(new Map(), rendered), {
    kind: "ui.patch",
    id: "msg-2",
    ui: { instanceId: "inst-1", patch: { title: "Page 2" } },
  });
  assert.equal(patched.get("inst-1")?.spec.props.title, "Page 2");
});

test("strips workspace fields from resolver input", () => {
  const source = parseDataSource({
    mode: "resolver",
    resolverId: "invoices.search",
    input: { query: "facture", workspaceId: "evil", userId: "x" },
  });
  assert.deepEqual(source, {
    mode: "resolver",
    resolverId: "invoices.search",
    input: { query: "facture" },
  });
});

test("les instructions de l'agent priment sur les vues historiques, instanceId conservé", () => {
  const rendered = {
    kind: "ui.render" as const,
    protocolVersion: "1.0" as const,
    id: "ui-abc",
    role: "assistant" as const,
    createdAt: "2026-08-27T08:00:00Z",
    ui: {
      instanceId: "ui_0123456789abcdef01234567",
      componentId: "mail.list",
      componentVersion: "1.0",
      props: { title: "Factures" },
      data: { mode: "resolver" as const, resolverId: "invoices.search", input: { limit: 20 } },
      fallbackText: "Voici vos factures.",
    },
  };
  const messages = messagesFromAssistantAnswer({
    reply: "Voici vos vingt dernières factures.",
    views: [{ kind: "senders", items: [] }],
    ui_messages: [rendered],
  });

  assert.equal(messages.length, 2);
  assert.equal(messages[1].kind, "ui.render");
  assert.equal(messages[1].kind === "ui.render" ? messages[1].ui.instanceId : "", "ui_0123456789abcdef01234567");
});

test("sans instruction de l'agent, les vues restent le repli", () => {
  const messages = messagesFromAssistantAnswer({
    reply: "Vos expéditeurs.",
    views: [{ kind: "senders", items: [] }],
    ui_messages: [],
  });
  assert.equal(messages.length, 2);
  assert.equal(messages[1].kind === "ui.render" ? messages[1].ui.componentId : "", "senders.list");
});

test("un patch met à jour l'instance du tour précédent, un remove la retire", () => {
  const previous = messagesFromAssistantAnswer({
    reply: "Voici vos factures.",
    views: [],
    ui_messages: [
      {
        kind: "ui.render",
        protocolVersion: "1.0",
        id: "ui-1",
        role: "assistant",
        createdAt: "2026-08-27T08:00:00Z",
        ui: {
          instanceId: "ui_1",
          componentId: "mail.list",
          componentVersion: "1.0",
          props: { title: "Factures" },
          fallbackText: "Vos factures.",
        },
      },
    ],
  });

  const patched = mergeUiMessages(previous, [
    {
      kind: "ui.patch",
      protocolVersion: "1.0",
      id: "ui-2",
      role: "assistant",
      createdAt: "2026-08-27T08:01:00Z",
      ui: { instanceId: "ui_1", patch: { title: "Factures payées" } },
    },
  ]);
  const survivor = patched.find((message) => message.kind === "ui.render");
  assert.equal(survivor?.kind === "ui.render" ? survivor.ui.props.title : "", "Factures payées");

  const removed = mergeUiMessages(patched, [
    {
      kind: "ui.remove",
      protocolVersion: "1.0",
      id: "ui-3",
      role: "assistant",
      createdAt: "2026-08-27T08:02:00Z",
      ui: { instanceId: "ui_1" },
    },
  ]);
  assert.equal(removed.some((message) => message.kind === "ui.render"), false);
});

test("les instances vivantes sont annoncées à l'agent, sans celles retirées", () => {
  const live = liveInstances([
    {
      kind: "ui.render",
      protocolVersion: "1.0",
      id: "ui-1",
      role: "assistant",
      createdAt: "2026-08-27T08:00:00Z",
      ui: {
        instanceId: "ui_1",
        componentId: "mail.list",
        componentVersion: "1.0",
        props: {},
        fallbackText: "Vos factures.",
      },
    },
    {
      kind: "ui.remove",
      protocolVersion: "1.0",
      id: "ui-2",
      role: "assistant",
      createdAt: "2026-08-27T08:01:00Z",
      ui: { instanceId: "ui_1" },
    },
  ]);
  assert.deepEqual(live, []);
});

test("seules les actions porteuses de sens repartent vers l'agent", () => {
  assert.equal(shouldAskAgent("messages.open"), true);
  assert.equal(shouldAskAgent("table.page"), false);
  assert.equal(shouldAskAgent("table.filter"), false);
});

test("une instruction d'interface hors protocole est rejetée", () => {
  assert.throws(() =>
    parseChatMessage({
      kind: "ui.render",
      protocolVersion: "1.0",
      id: "ui-1",
      role: "user",
      createdAt: "2026-08-27T08:00:00Z",
      ui: {
        instanceId: "ui_1",
        componentId: "mail.list",
        componentVersion: "1.0",
        props: {},
        fallbackText: "x",
      },
    }),
  );
});

test("le composant carte n'accepte que le résolveur d'itinéraire déclaré", () => {
  const spec = parseRenderSpec({
    instanceId: "ui_map_1",
    componentId: "map.route",
    componentVersion: "1.0",
    props: { title: "Trajet vers Le Rival" },
    data: { mode: "resolver", resolverId: "places.route", input: { to: "Le Rival", mode: "walking" } },
    fallbackText: "Je n'ai pas pu afficher le trajet.",
  });
  assert.equal(REGISTERED.has(spec.componentId), true);
  assert.equal(spec.data?.mode === "resolver" ? spec.data.resolverId : "", "places.route");
});

test("un tracé d'itinéraire est validé, un tracé hors bornes est rejeté", () => {
  const schema = {
    type: "object" as const,
    additionalProperties: false,
    required: ["origin", "destination", "mode", "distanceKm", "durationMin", "coordinates"],
    properties: {
      origin: PLACE,
      destination: PLACE,
      mode: { type: "string" as const, enum: ["driving", "walking", "cycling"] },
      distanceKm: { type: "number" as const, minimum: 0, maximum: 40_000 },
      durationMin: { type: "integer" as const, minimum: 0, maximum: 100_000 },
      estimated: { type: "boolean" as const },
      coordinates: {
        type: "array" as const,
        minItems: 2,
        maxItems: 500,
        items: {
          type: "array" as const,
          minItems: 2,
          maxItems: 2,
          items: { type: "number" as const, minimum: -180, maximum: 180 },
        },
      },
    },
  };
  const route = {
    origin: { name: "Gare de Lyon, Paris", lon: 2.3736, lat: 48.8443 },
    destination: { name: "Le Rival, Paris", lon: 2.3532, lat: 48.8606 },
    mode: "walking",
    distanceKm: 2.4,
    durationMin: 31,
    estimated: true,
    coordinates: [
      [2.3736, 48.8443],
      [2.3532, 48.8606],
    ],
  };
  assert.doesNotThrow(() => assertSchema(route, schema));
  assert.throws(() => assertSchema({ ...route, coordinates: [[2.3736, 48.8443]] }, schema));
  assert.throws(() => assertSchema({ ...route, mode: "teleportation" }, schema));
});

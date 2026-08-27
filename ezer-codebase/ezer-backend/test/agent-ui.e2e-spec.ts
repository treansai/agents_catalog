import { createHash } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { INestApplication } from "@nestjs/common";
import request from "supertest";

import { createEzerApplication } from "../src/bootstrap";
import type { EmailAnalysis } from "../src/domain/models";

const API_KEY = "test-api-key";

function hex(seed: string): string {
  return createHash("sha256").update(seed).digest("hex");
}

function receipt(accountId: string, index: number): EmailAnalysis {
  const id = hex(`receipt:${accountId}:${index}`);
  return {
    analysis_id: id,
    message_ref: `outlook:${accountId}:invoice-${index}`,
    content_hash: hex(`hash:${accountId}:${index}`),
    pipeline_version: "ezer-ts-v1",
    model_id: "ezer-typescript-rules-v1",
    prompt_version: "heuristic-fr-v1",
    created_at: `2026-08-27T08:${String(index % 60).padStart(2, "0")}:00.000Z`,
    category: "receipt",
    priority: "normal",
    needs_human_review: false,
    summary: `Facture ${index} — paiement reçu`,
    key_points: [`Montant de la facture ${index}`],
    action_items: [],
    safety: {
      risk_level: "none",
      prompt_injection_detected: false,
      phishing_likelihood: 0.01,
      indicators: [],
      rationale: "Reçu de paiement.",
      confidence: 0.9
    },
    triage: {
      category: "receipt",
      priority: "normal",
      needs_human_review: false,
      confidence: 0.9,
      rationale: "Facture"
    },
    detected_language: "fr"
  };
}

describe("Agent UI runtime", () => {
  let application: INestApplication;
  let workingDirectory: string;

  beforeEach(async () => {
    workingDirectory = await mkdtemp(join(tmpdir(), "ezer-agent-ui-"));
    const accounts = [
      { id: "workspace-a", provider: "outlook" as const },
      { id: "workspace-b", provider: "gmail" as const }
    ];
    await writeFile(
      join(workingDirectory, "ezer.json"),
      `${JSON.stringify(
        {
          schema_version: 1,
          accounts,
          analyses: [
            ...Array.from({ length: 24 }, (_, index) => receipt("workspace-a", index)),
            receipt("workspace-b", 99)
          ],
          cursors: { "workspace-a": null, "workspace-b": null }
        },
        null,
        2
      )}\n`,
      { mode: 0o600 }
    );
    process.env.NODE_ENV = "test";
    process.env.EZER_MODE = "configured";
    process.env.EZER_API_KEY = API_KEY;
    process.env.EZER_DATA_FILE = join(workingDirectory, "ezer.json");
    process.env.EZER_ACCOUNTS_JSON = JSON.stringify(accounts);
    delete process.env.EZER_SOURCE_FILE;
    delete process.env.EZER_OUTLOOK_CLIENT_ID;
    application = await createEzerApplication();
    await application.init();
  });

  afterEach(async () => {
    await application.close();
    await rm(workingDirectory, { recursive: true, force: true });
  });

  function get(path: string) {
    return request(application.getHttpServer()).get(path).set("X-API-Key", API_KEY);
  }

  function post(path: string, body: string | object) {
    return request(application.getHttpServer())
      .post(path)
      .set("X-API-Key", API_KEY)
      .set("Content-Type", "application/json")
      .send(body);
  }

  it("renvoie un catalogue sans chemins d'import", async () => {
    const response = await get("/v1/agent-ui/catalog?workspace_id=workspace-a").expect(200);
    expect(response.body.protocolVersion).toBe("1.0");
    expect(response.body.components.map((entry: { id: string }) => entry.id)).toContain("mail.list");
    expect(JSON.stringify(response.body)).not.toMatch(/import\(|graph\.microsoft|EZER_API_KEY/);
  });

  it("résout une liste de factures paginée dans le workspace courant", async () => {
    const response = await post("/v1/agent-ui/resolve?workspace_id=workspace-a", {
      resolverId: "invoices.search",
      componentId: "mail.list",
      instanceId: "inst-invoices",
      input: { limit: 10, offset: 0, query: "facture" }
    }).expect(200);

    expect(response.body.status).toBe("success");
    expect(response.body.data.items).toHaveLength(10);
    expect(response.body.data.total).toBe(24);
    expect(response.body.data.items[0].subject).toMatch(/Facture/);
  });

  it("isole les données entre deux workspaces", async () => {
    const alpha = await post("/v1/agent-ui/resolve?workspace_id=workspace-a", {
      resolverId: "invoices.search",
      componentId: "mail.list",
      instanceId: "a",
      input: { limit: 25, offset: 0 }
    }).expect(200);
    const beta = await post("/v1/agent-ui/resolve?workspace_id=workspace-b", {
      resolverId: "invoices.search",
      componentId: "mail.list",
      instanceId: "b",
      input: { limit: 25, offset: 0 }
    }).expect(200);

    expect(alpha.body.data.total).toBe(24);
    expect(beta.body.data.total).toBe(1);
    expect(beta.body.data.items[0].id).toBe("invoice-99");
  });

  it("refuse un résolveur non autorisé pour le composant", async () => {
    await post("/v1/agent-ui/resolve?workspace_id=workspace-a", {
      resolverId: "messages.get",
      componentId: "mail.list",
      instanceId: "x",
      input: { message_id: "nope" }
    }).expect(400);
  });

  it("refuse un componentId inconnu", async () => {
    await post("/v1/agent-ui/resolve?workspace_id=workspace-a", {
      resolverId: "invoices.search",
      componentId: "evil.widget",
      instanceId: "x",
      input: {}
    }).expect(400);
  });

  it("refuse un workspace inconnu", async () => {
    await get("/v1/agent-ui/catalog?workspace_id=workspace-z").expect(403);
  });

  it("pagine une grande table côté serveur", async () => {
    const page = await post("/v1/agent-ui/resolve?workspace_id=workspace-a", {
      resolverId: "invoices.search",
      componentId: "mail.list",
      instanceId: "page-2",
      input: { limit: 10, offset: 10, query: "facture" }
    }).expect(200);
    expect(page.body.data.items).toHaveLength(10);
    expect(page.body.data.offset).toBe(10);
    expect(page.body.data.items[0].id).not.toBe("invoice-0");
  });

  it("expose une carte métrique de reçus", async () => {
    const response = await post("/v1/agent-ui/resolve?workspace_id=workspace-a", {
      resolverId: "metrics.receipts",
      componentId: "metric.card",
      instanceId: "metric-1",
      input: {}
    }).expect(200);
    expect(response.body.data.label).toBe("reçus ce mois");
    expect(response.body.data.value).toBe("24");
  });

  it("refuse une action inconnue", async () => {
    await post("/v1/agent-ui/action?workspace_id=workspace-a", {
      kind: "ui.action",
      eventId: "evt-1",
      messageId: "msg-1",
      instanceId: "inst-1",
      componentId: "mail.list",
      componentVersion: "1.0",
      actionId: "rm -rf",
      values: {},
      idempotencyKey: "idem-unknown"
    }).expect(400);
  });

  it("exige une confirmation avant mutation et protège le double clic", async () => {
    const payload = {
      kind: "ui.action",
      eventId: "evt-del-1",
      messageId: "msg-1",
      instanceId: "inst-1",
      componentId: "mail.list",
      componentVersion: "1.0",
      actionId: "messages.trash",
      values: { targetId: "invoice-1" },
      idempotencyKey: "idem-del-1"
    };
    const first = await post("/v1/agent-ui/action?workspace_id=workspace-a", payload).expect(200);
    const second = await post("/v1/agent-ui/action?workspace_id=workspace-a", payload).expect(200);

    expect(first.body.status).toBe("confirmation_required");
    expect(first.body.confirmation.reversible).toBe(true);
    expect(second.body.confirmation.confirmationId).toBe(first.body.confirmation.confirmationId);

    await post("/v1/agent-ui/action?workspace_id=workspace-a", {
      kind: "ui.action",
      eventId: "evt-del-bad",
      messageId: "msg-1",
      instanceId: "inst-1",
      componentId: "confirm.dialog",
      componentVersion: "1.0",
      actionId: "confirmation.confirm",
      values: {
        confirmationId: first.body.confirmation.confirmationId,
        confirmationToken: "not-the-token"
      },
      idempotencyKey: "idem-del-bad"
    }).expect(409);
  });

  it("n'exécute pas de JavaScript fourni dans un payload", async () => {
    await post("/v1/agent-ui/resolve?workspace_id=workspace-a", {
      resolverId: "invoices.search",
      componentId: "mail.list",
      instanceId: "xss",
      input: { query: "<script>alert(1)</script>", extra: "nope", constructor: { prototype: { polluted: true } } }
    }).expect(400);
  });

  it("accepte un clic déclaratif sur une ligne", async () => {
    const response = await post("/v1/agent-ui/action?workspace_id=workspace-a", {
      kind: "ui.action",
      eventId: "evt-open-1",
      messageId: "msg-1",
      instanceId: "inst-1",
      componentId: "mail.list",
      componentVersion: "1.0",
      actionId: "messages.open",
      values: { targetId: "invoice-1" },
      idempotencyKey: "idem-open-1"
    }).expect(200);
    expect(response.body.status).toBe("success");
    expect(response.body.result.targetId).toBe("invoice-1");
  });

  it("filtre le catalogue par capacité et par mots-clés", async () => {
    const response = await get(
      "/v1/agent-ui/catalog?workspace_id=workspace-a&query=tableau%20de%20factures&capabilities=display_table&limit=5"
    ).expect(200);
    const ids = response.body.components.map((entry: { id: string }) => entry.id);
    expect(ids).toContain("mail.list");
    expect(ids).not.toContain("confirm.dialog");
    expect(response.body.components.length).toBeLessThanOrEqual(5);
  });

  it("valide une proposition d'affichage et frappe l'instanceId côté serveur", async () => {
    const response = await post("/v1/agent-ui/render?workspace_id=workspace-a", {
      componentId: "mail.list",
      componentVersion: "1.0",
      props: { title: "Mes dernières factures", pageSize: 20 },
      data: {
        mode: "resolver",
        resolverId: "invoices.search",
        input: { limit: 20, offset: 0, query: "facture" }
      },
      fallbackText: "Voici vos dernières factures."
    }).expect(200);

    expect(response.body.ui.instanceId).toMatch(/^ui_[0-9a-f]{24}$/);
    expect(response.body.ui.componentId).toBe("mail.list");
    expect(response.body.ui.componentVersion).toBe("1.0");
    expect(response.body.ui.data.resolverId).toBe("invoices.search");
  });

  it("refuse de rendre un composant inventé", async () => {
    await post("/v1/agent-ui/render?workspace_id=workspace-a", {
      componentId: "billing.invoice-table",
      props: {},
      fallbackText: "Voici vos factures."
    }).expect(400);
  });

  it("refuse des props hors schéma", async () => {
    await post("/v1/agent-ui/render?workspace_id=workspace-a", {
      componentId: "mail.list",
      props: { title: "ok", onClick: "alert(1)" },
      fallbackText: "Liste."
    }).expect(400);
  });

  it("refuse un résolveur non autorisé au moment du rendu", async () => {
    await post("/v1/agent-ui/render?workspace_id=workspace-a", {
      componentId: "metric.card",
      props: { label: "Reçus" },
      data: { mode: "resolver", resolverId: "messages.get", input: { message_id: "x" } },
      fallbackText: "Métrique."
    }).expect(400);
  });

  it("refuse une version de composant obsolète", async () => {
    await post("/v1/agent-ui/render?workspace_id=workspace-a", {
      componentId: "mail.list",
      componentVersion: "0.9",
      props: {},
      fallbackText: "Liste."
    }).expect(409);
  });

  it("retire l'identité fournie par le modèle dans l'entrée d'un résolveur", async () => {
    const response = await post("/v1/agent-ui/render?workspace_id=workspace-a", {
      componentId: "mail.list",
      props: {},
      data: {
        mode: "resolver",
        resolverId: "invoices.search",
        input: { limit: 5, workspaceId: "workspace-b", account_id: "workspace-b", userId: "root" }
      },
      fallbackText: "Liste."
    }).expect(200);

    expect(response.body.ui.data.input).toEqual({ limit: 5 });
  });

  it("valide un patch de props partiel", async () => {
    const response = await post("/v1/agent-ui/patch?workspace_id=workspace-a", {
      instanceId: "ui_0123456789abcdef01234567",
      componentId: "mail.list",
      componentVersion: "1.0",
      props: { title: "Factures de juillet" }
    }).expect(200);
    expect(response.body.ui).toEqual({
      instanceId: "ui_0123456789abcdef01234567",
      patch: { title: "Factures de juillet" }
    });

    await post("/v1/agent-ui/patch?workspace_id=workspace-a", {
      instanceId: "ui_0123456789abcdef01234567",
      componentId: "mail.list",
      props: { unknownProp: 1 }
    }).expect(400);
  });

  it("expose le composant carte et son résolveur d'itinéraire", async () => {
    const response = await get(
      "/v1/agent-ui/catalog?workspace_id=workspace-a&query=trajet%20restaurant&capabilities=display_map"
    ).expect(200);
    const map = response.body.components.find((entry: { id: string }) => entry.id === "map.route");
    expect(map.allowedDataResolvers).toEqual(["places.route"]);
    expect(JSON.stringify(map)).not.toMatch(/maptiler|api\.|key=/i);
  });

  it("trace un itinéraire vers un lieu nommé par l'agent", async () => {
    const response = await post("/v1/agent-ui/resolve?workspace_id=workspace-a", {
      resolverId: "places.route",
      componentId: "map.route",
      instanceId: "inst-map",
      input: { to: "Le Rival", from: "Gare de Lyon", mode: "walking" }
    }).expect(200);

    const data = response.body.data;
    expect(data.destination.name).toMatch(/Rival/);
    expect(data.origin.name).toMatch(/Gare de Lyon/);
    expect(data.mode).toBe("walking");
    expect(data.distanceKm).toBeGreaterThan(0);
    expect(data.durationMin).toBeGreaterThan(0);
    expect(data.coordinates.length).toBeGreaterThanOrEqual(2);
    // Sans service de routage configuré, la sortie s'annonce comme une estimation.
    expect(data.estimated).toBe(true);
  });

  it("affiche une carte de trajet validée par le catalogue", async () => {
    const response = await post("/v1/agent-ui/render?workspace_id=workspace-a", {
      componentId: "map.route",
      componentVersion: "1.0",
      props: { title: "Trajet vers Le Rival" },
      data: {
        mode: "resolver",
        resolverId: "places.route",
        input: { to: "Le Rival, Paris", mode: "walking" }
      },
      fallbackText: "Je n'ai pas pu afficher le trajet."
    }).expect(200);
    expect(response.body.ui.componentId).toBe("map.route");
    expect(response.body.ui.data.input).toEqual({ to: "Le Rival, Paris", mode: "walking" });
  });

  it("refuse une entrée d'itinéraire hors schéma, URL comprise", async () => {
    await post("/v1/agent-ui/resolve?workspace_id=workspace-a", {
      resolverId: "places.route",
      componentId: "map.route",
      instanceId: "inst-map",
      input: { to: "Le Rival", endpoint: "http://169.254.169.254/latest/meta-data" }
    }).expect(400);

    await post("/v1/agent-ui/resolve?workspace_id=workspace-a", {
      resolverId: "places.route",
      componentId: "map.route",
      instanceId: "inst-map",
      input: { to: "Le Rival", mode: "teleportation" }
    }).expect(400);
  });

  it("refuse un lieu introuvable sans inventer de coordonnées", async () => {
    await post("/v1/agent-ui/resolve?workspace_id=workspace-a", {
      resolverId: "places.route",
      componentId: "map.route",
      instanceId: "inst-map",
      input: { to: "Restaurant totalement inexistant zzz" }
    }).expect(422);
  });

  it("n'autorise pas le résolveur d'itinéraire sur un autre composant", async () => {
    await post("/v1/agent-ui/resolve?workspace_id=workspace-a", {
      resolverId: "places.route",
      componentId: "mail.list",
      instanceId: "inst-map",
      input: { to: "Le Rival" }
    }).expect(400);
  });
});

import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { ValidationPipe, type INestApplication } from "@nestjs/common";
import { Test } from "@nestjs/testing";
import request from "supertest";

import { AppModule } from "../src/app.module";
import { NeutralExceptionFilter } from "../src/common/neutral-exception.filter";
import { HTTP_FETCH } from "../src/mail/outlook-auth.service";

const API_KEY = "test-api-key";
const MAILBOX = "marctelly@hotmail.fr";
const MESSAGE_ID = "AAMkAGI1";

interface GraphCall {
  url: string;
  method: string;
  body: string;
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

function graphDouble(options: { tokenResponse?: () => Response } = {}) {
  const calls: GraphCall[] = [];
  const message = {
    id: MESSAGE_ID,
    conversationId: "AAQkAGI1",
    subject: "Offre exceptionnelle, agissez vite",
    from: { emailAddress: { name: "Promo", address: "promo@example.com" } },
    receivedDateTime: "2026-08-27T08:15:00Z",
    bodyPreview: "Cliquez ici",
    isRead: false,
    hasAttachments: false,
    body: { contentType: "text", content: "Corps du message." }
  };
  const fetchDouble = (url: string, init: RequestInit): Promise<Response> => {
    calls.push({ url, method: String(init.method), body: String(init.body ?? "") });
    if (url.endsWith("/token")) {
      return Promise.resolve(
        options.tokenResponse?.() ??
          jsonResponse({ access_token: "graph-access-token", expires_in: 3600 })
      );
    }
    if (url.includes("/messages/") && url.endsWith("/move")) {
      return Promise.resolve(jsonResponse({ id: "moved-id" }));
    }
    if (url.includes("/mailFolders/inbox/messages")) {
      return Promise.resolve(jsonResponse({ value: [message] }));
    }
    if (url.includes("/mailFolders")) {
      return Promise.resolve(
        jsonResponse({
          value: [{ displayName: "Boîte de réception", totalItemCount: 42, unreadItemCount: 7 }]
        })
      );
    }
    if (url.includes("/me/messages")) {
      return Promise.resolve(
        url.includes("%24search")
          ? jsonResponse({ value: [message] })
          : jsonResponse(message)
      );
    }
    return Promise.resolve(jsonResponse({ error: "unexpected" }, 404));
  };
  return { calls, fetchDouble };
}

describe("Accès aux messages pour les agents", () => {
  let application: INestApplication;
  let workingDirectory: string;
  let graph: ReturnType<typeof graphDouble>;

  async function start(graphOptions: { tokenResponse?: () => Response } = {}): Promise<void> {
    graph = graphDouble(graphOptions);
    const moduleRef = await Test.createTestingModule({ imports: [AppModule] })
      .overrideProvider(HTTP_FETCH)
      .useValue(graph.fetchDouble)
      .compile();
    application = moduleRef.createNestApplication({ logger: false });
    application.useGlobalPipes(
      new ValidationPipe({ transform: true, whitelist: true, forbidNonWhitelisted: true })
    );
    application.useGlobalFilters(application.get(NeutralExceptionFilter));
    await application.init();
  }

  function get(path: string) {
    return request(application.getHttpServer()).get(path).set("X-API-Key", API_KEY);
  }

  beforeEach(async () => {
    workingDirectory = await mkdtemp(join(tmpdir(), "ezer-mail-tools-test-"));
    await writeFile(
      join(workingDirectory, "tokens.json"),
      JSON.stringify({
        schema_version: 1,
        tokens: [
          {
            account_id: "outlook-perso",
            mailbox: MAILBOX,
            refresh_token: "refresh-token",
            connected_at: "2026-08-27T16:19:18.536Z",
            scopes: "offline_access Mail.ReadWrite User.Read"
          }
        ]
      }),
      { mode: 0o600 }
    );
    process.env.NODE_ENV = "test";
    process.env.EZER_MODE = "configured";
    process.env.EZER_API_KEY = API_KEY;
    process.env.EZER_DATA_FILE = join(workingDirectory, "ezer.json");
    process.env.EZER_TOKEN_FILE = join(workingDirectory, "tokens.json");
    process.env.EZER_OUTLOOK_CLIENT_ID = "9c82235d-0527-40f5-bdc2-bfe493fcbc3c";
    process.env.EZER_ACCOUNTS_JSON = JSON.stringify([
      { id: "outlook-perso", provider: "outlook", mailbox: MAILBOX }
    ]);
    delete process.env.EZER_SOURCE_FILE;
  });

  afterEach(async () => {
    await application?.close();
    await rm(workingDirectory, { recursive: true, force: true });
  });

  it("liste les messages les plus récents d’abord", async () => {
    await start();

    const response = await get("/v1/accounts/outlook-perso/messages?top=5").expect(200);

    expect(response.body.messages[0]).toMatchObject({
      message_id: MESSAGE_ID,
      sender_address: "promo@example.com",
      is_read: false
    });
    expect(
      graph.calls.some((call) => call.url.includes("%24orderby=receivedDateTime+desc"))
    ).toBe(true);
  });

  it("recherche, lit un message et renvoie l’état de la boîte", async () => {
    await start();

    const found = await get(
      "/v1/accounts/outlook-perso/messages?query=promo&top=5"
    ).expect(200);
    expect(found.body.messages).toHaveLength(1);
    expect(graph.calls.some((call) => call.url.includes("%24search"))).toBe(true);

    const message = await get(
      `/v1/accounts/outlook-perso/message?message_id=${MESSAGE_ID}`
    ).expect(200);
    expect(message.body.body_text).toBe("Corps du message.");

    const stats = await get("/v1/accounts/outlook-perso/mailbox-stats").expect(200);
    expect(stats.body).toMatchObject({ total_messages: 42, unread_messages: 7 });
  });

  it("met un message à la corbeille, jamais en suppression définitive", async () => {
    await start();

    const response = await request(application.getHttpServer())
      .post("/v1/accounts/outlook-perso/message/trash")
      .set("X-API-Key", API_KEY)
      .set("Content-Type", "application/json")
      .send({ message_id: MESSAGE_ID })
      .expect(200);

    expect(response.body).toEqual({ message_id: MESSAGE_ID, moved_to: "deleteditems" });
    const move = graph.calls.find((call) => call.url.endsWith("/move"));
    expect(move?.body).toContain("deleteditems");
    expect(graph.calls.some((call) => call.url.includes("permanentDelete"))).toBe(false);
  });

  it("filtre et trie à la source plutôt qu’après coup", async () => {
    await start();

    await get(
      "/v1/accounts/outlook-perso/messages?top=5&unread_only=true" +
        "&from_address=promo@example.com&since=2026-08-01&order=asc"
    ).expect(200);

    const call = graph.calls.find((entry) => entry.url.includes("mailFolders/inbox/messages"));
    const parameters = new URLSearchParams(call?.url.split("?")[1] ?? "");
    expect(parameters.get("$orderby")).toBe("receivedDateTime asc");
    const filter = parameters.get("$filter") ?? "";
    expect(filter).toContain("isRead eq false");
    expect(filter).toContain("from/emailAddress/address eq 'promo@example.com'");
    expect(filter).toContain("receivedDateTime ge 2026-08-01T00:00:00.000Z");
  });

  it("agrège les messages récents par expéditeur", async () => {
    await start();

    const response = await get("/v1/accounts/outlook-perso/senders?sample=10").expect(200);

    expect(response.body.senders).toEqual([
      { sender_address: "promo@example.com", sender_name: "Promo", total: 1, unread: 1 }
    ]);
  });

  it("refuse un expéditeur ou une date qui ne sont pas des valeurs simples", async () => {
    await start();

    // Une apostrophe fermerait le littéral du $filter OData : la valeur est rejetée avant l'appel.
    await get("/v1/accounts/outlook-perso/messages?from_address=a'%20or%201%20eq%201").expect(400);
    await get("/v1/accounts/outlook-perso/messages?since=hier").expect(400);
  });

  it("renouvelle sur les portées consenties, pas sur celles que réclame le code", async () => {
    await start();

    await get("/v1/accounts/outlook-perso/messages?top=3").expect(200);

    const refresh = graph.calls.find((call) => call.url.endsWith("/token"));
    expect(refresh?.body).toContain("Mail.ReadWrite");
    expect(refresh?.body).toContain("grant_type=refresh_token");
  });

  it("normalise les portées de renouvellement renvoyées par Microsoft", async () => {
    // Ce que Microsoft renvoie réellement : pas d'`offline_access`, et `profile` seule — que
    // l'endpoint refuse sans `openid`. Le renouvellement ne doit pas rejouer cette chaîne telle
    // quelle, sinon une connexion valide devient inutilisable.
    await rm(join(workingDirectory, "tokens.json"), { force: true });
    await writeFile(
      join(workingDirectory, "tokens.json"),
      JSON.stringify({
        schema_version: 1,
        tokens: [
          {
            account_id: "outlook-perso",
            mailbox: MAILBOX,
            refresh_token: "refresh-token",
            connected_at: "2026-08-27T16:19:18.536Z",
            scopes: "Mail.ReadWrite User.Read Mail.Read profile"
          }
        ]
      }),
      { mode: 0o600 }
    );
    await start();

    await get("/v1/accounts/outlook-perso/messages?top=3").expect(200);

    const scope = new URLSearchParams(
      graph.calls.find((call) => call.url.endsWith("/token"))?.body ?? ""
    ).get("scope");
    expect(scope?.split(" ")).toEqual(
      expect.arrayContaining(["offline_access", "Mail.ReadWrite", "User.Read"])
    );
    expect(scope).not.toContain("profile");
  });

  it("conserve la connexion quand un renouvellement échoue sans révocation", async () => {
    // Portée refusée : incident de configuration, pas une révocation du consentement.
    await start({ tokenResponse: () => jsonResponse({ error: "invalid_scope" }, 400) });

    await get("/v1/accounts/outlook-perso/messages?top=3").expect(422);

    const accounts = await get("/v1/accounts").expect(200);
    expect(accounts.body.accounts[0]).toMatchObject({ status: "connected" });
  });

  it("n’oublie le jeton que sur une révocation explicite de Microsoft", async () => {
    await start({ tokenResponse: () => jsonResponse({ error: "invalid_grant" }, 400) });

    await get("/v1/accounts/outlook-perso/messages?top=3").expect(422);

    const accounts = await get("/v1/accounts").expect(200);
    expect(accounts.body.accounts[0]).toMatchObject({ status: "disconnected" });
  });

  it("exige la clé d’API, un compte connu et un identifiant valide", async () => {
    await start();

    await request(application.getHttpServer())
      .get("/v1/accounts/outlook-perso/messages")
      .expect(401);
    await get("/v1/accounts/inconnu/messages").expect(404);
    await get("/v1/accounts/outlook-perso/message?message_id=../../secret").expect(400);
  });
});

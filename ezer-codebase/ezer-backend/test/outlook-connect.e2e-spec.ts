import { mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { ValidationPipe, type INestApplication } from "@nestjs/common";
import { Test } from "@nestjs/testing";
import request from "supertest";

import { AppModule } from "../src/app.module";
import { NeutralExceptionFilter } from "../src/common/neutral-exception.filter";
import { HTTP_FETCH, SLEEPER } from "../src/mail/outlook-auth.service";

const API_KEY = "test-api-key";
const MAILBOX = "marctelly@outlook.com";
const CLIENT_ID = "9c82235d-0527-40f5-bdc2-bfe493fcbc3c";

interface FetchCall {
  url: string;
  fields: Record<string, string>;
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

/** Microsoft simulé : device code, une attente d'autorisation, puis un jeton délégué. */
function microsoftDouble(options: { signedInMailbox?: string } = {}) {
  const calls: FetchCall[] = [];
  let tokenCalls = 0;
  const fetchDouble = (url: string, init: RequestInit): Promise<Response> => {
    const fields = Object.fromEntries(new URLSearchParams(String(init.body ?? "")));
    calls.push({ url, fields });
    if (url.endsWith("/devicecode")) {
      return Promise.resolve(
        jsonResponse({
          device_code: "device-code-secret",
          user_code: "ABCD-EFGH",
          verification_uri: "https://microsoft.com/link",
          expires_in: 900,
          interval: 5
        })
      );
    }
    if (url.endsWith("/token")) {
      tokenCalls += 1;
      if (tokenCalls === 1) return Promise.resolve(jsonResponse({ error: "authorization_pending" }, 400));
      return Promise.resolve(
        jsonResponse({
          access_token: "graph-access-token",
          refresh_token: "graph-refresh-token",
          expires_in: 3600
        })
      );
    }
    if (url.startsWith("https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages")) {
      return Promise.resolve(
        jsonResponse({
          value: [
            {
              id: "AAMkAGI1",
              conversationId: "AAQkAGI1",
              subject: "Facture à régler avant vendredi",
              from: { emailAddress: { name: "Comptabilité", address: "compta@example.com" } },
              receivedDateTime: "2026-08-27T08:15:00Z",
              bodyPreview: "Merci de régler la facture 2026-118.",
              body: { contentType: "text", content: "Merci de régler la facture 2026-118." }
            }
          ]
        })
      );
    }
    if (url.startsWith("https://graph.microsoft.com/v1.0/me")) {
      return Promise.resolve(jsonResponse({ mail: options.signedInMailbox ?? MAILBOX }));
    }
    return Promise.resolve(jsonResponse({ error: "unexpected_endpoint" }, 404));
  };
  return { calls, fetchDouble };
}

async function settledConnection(
  application: INestApplication
): Promise<Record<string, unknown>> {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const response = await request(application.getHttpServer())
      .get("/v1/accounts/outlook-perso/connection")
      .set("X-API-Key", API_KEY)
      .expect(200);
    const body = response.body as Record<string, unknown>;
    if (body.status !== "pending") return body;
    await new Promise((resolve) => setImmediate(resolve));
  }
  throw new Error("the device-code flow never settled");
}

describe("Connexion Outlook", () => {
  let application: INestApplication;
  let workingDirectory: string;
  let tokenFile: string;

  async function start(
    fetchDouble: (url: string, init: RequestInit) => Promise<Response>,
    environment: Record<string, string | undefined> = {}
  ): Promise<void> {
    for (const [name, value] of Object.entries(environment)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
    const moduleRef = await Test.createTestingModule({ imports: [AppModule] })
      .overrideProvider(HTTP_FETCH)
      .useValue(fetchDouble)
      .overrideProvider(SLEEPER)
      .useValue(() => Promise.resolve())
      .compile();
    application = moduleRef.createNestApplication({ logger: false });
    application.useGlobalPipes(
      new ValidationPipe({ transform: true, whitelist: true, forbidNonWhitelisted: true })
    );
    application.useGlobalFilters(application.get(NeutralExceptionFilter));
    await application.init();
  }

  beforeEach(async () => {
    workingDirectory = await mkdtemp(join(tmpdir(), "ezer-connect-test-"));
    tokenFile = join(workingDirectory, "tokens.json");
    process.env.NODE_ENV = "test";
    process.env.EZER_MODE = "configured";
    process.env.EZER_API_KEY = API_KEY;
    process.env.EZER_DATA_FILE = join(workingDirectory, "ezer.json");
    process.env.EZER_TOKEN_FILE = tokenFile;
    process.env.EZER_OUTLOOK_CLIENT_ID = CLIENT_ID;
    process.env.EZER_ACCOUNTS_JSON = JSON.stringify([
      { id: "outlook-perso", provider: "outlook", mailbox: MAILBOX }
    ]);
    delete process.env.EZER_SOURCE_FILE;
  });

  afterEach(async () => {
    await application?.close();
    await rm(workingDirectory, { recursive: true, force: true });
  });

  it("expose le code à saisir sans jamais révéler le device code", async () => {
    const { calls, fetchDouble } = microsoftDouble();
    await start(fetchDouble);

    const started = await request(application.getHttpServer())
      .post("/v1/accounts/outlook-perso/connection")
      .set("X-API-Key", API_KEY)
      .expect(200);

    expect(started.body).toMatchObject({
      account_id: "outlook-perso",
      provider: "outlook",
      mailbox: MAILBOX,
      status: "pending",
      verification_uri: "https://microsoft.com/link",
      user_code: "ABCD-EFGH"
    });
    expect(JSON.stringify(started.body)).not.toContain("device-code-secret");
    expect(calls[0]?.fields.scope).toContain("Mail.Read");
    expect(calls[0]?.fields.scope).toContain("offline_access");
  });

  it("connecte la boîte, conserve le refresh token hors du fichier de données et le protège", async () => {
    const { fetchDouble } = microsoftDouble();
    await start(fetchDouble);

    await request(application.getHttpServer())
      .post("/v1/accounts/outlook-perso/connection")
      .set("X-API-Key", API_KEY)
      .expect(200);

    expect(await settledConnection(application)).toMatchObject({
      status: "connected",
      code: null,
      verification_uri: null,
      user_code: null
    });

    const accounts = await request(application.getHttpServer())
      .get("/v1/accounts")
      .set("X-API-Key", API_KEY)
      .expect(200);
    expect(accounts.body.accounts).toEqual([
      expect.objectContaining({ id: "outlook-perso", mailbox: MAILBOX, status: "connected" })
    ]);

    const stored = JSON.parse(await readFile(tokenFile, "utf8"));
    expect(stored.tokens[0]).toMatchObject({
      account_id: "outlook-perso",
      mailbox: MAILBOX,
      refresh_token: "graph-refresh-token"
    });
    expect((await stat(tokenFile)).mode & 0o777).toBe(0o600);

    const dataFile = await readFile(process.env.EZER_DATA_FILE as string, "utf8");
    expect(dataFile).not.toContain("graph-refresh-token");
  });

  it("lit les messages de la boîte connectée via Microsoft Graph", async () => {
    const { calls, fetchDouble } = microsoftDouble();
    await start(fetchDouble);

    await request(application.getHttpServer())
      .post("/v1/accounts/outlook-perso/connection")
      .set("X-API-Key", API_KEY)
      .expect(200);
    await settledConnection(application);

    const synchronized = await request(application.getHttpServer())
      .post("/v1/sync")
      .set("X-API-Key", API_KEY)
      .set("Content-Type", "application/json")
      .send({ account_ids: ["outlook-perso"], limit: 10 })
      .expect(200);

    expect(synchronized.body.reports[0]).toMatchObject({
      account_id: "outlook-perso",
      provider: "outlook",
      fetched: 1,
      processed: 1,
      failed: 0
    });
    expect(synchronized.body.reports[0].analyses[0].message_ref).toContain("outlook-perso");

    const graphCall = calls.find((call) => call.url.includes("/mailFolders/inbox/messages"));
    expect(graphCall?.url).toContain("%24top=10");
  });

  it("refuse une autre boîte que celle configurée", async () => {
    const { fetchDouble } = microsoftDouble({ signedInMailbox: "quelquun.dautre@outlook.com" });
    await start(fetchDouble);

    await request(application.getHttpServer())
      .post("/v1/accounts/outlook-perso/connection")
      .set("X-API-Key", API_KEY)
      .expect(200);

    expect(await settledConnection(application)).toMatchObject({
      status: "failed",
      code: "mailbox_mismatch"
    });
    await expect(readFile(tokenFile, "utf8")).rejects.toMatchObject({ code: "ENOENT" });
  });

  it("signale une application publique non configurée sans divulguer la configuration", async () => {
    const { fetchDouble } = microsoftDouble();
    await start(fetchDouble, { EZER_OUTLOOK_CLIENT_ID: undefined });

    const started = await request(application.getHttpServer())
      .post("/v1/accounts/outlook-perso/connection")
      .set("X-API-Key", API_KEY)
      .expect(200);

    expect(started.body).toMatchObject({ status: "failed", code: "client_not_configured" });
  });

  it("déconnecte le compte et oublie son refresh token", async () => {
    const { fetchDouble } = microsoftDouble();
    await start(fetchDouble);

    await request(application.getHttpServer())
      .post("/v1/accounts/outlook-perso/connection")
      .set("X-API-Key", API_KEY)
      .expect(200);
    await settledConnection(application);

    const disconnected = await request(application.getHttpServer())
      .delete("/v1/accounts/outlook-perso/connection")
      .set("X-API-Key", API_KEY)
      .expect(200);

    expect(disconnected.body).toMatchObject({ status: "disconnected" });
    expect(JSON.parse(await readFile(tokenFile, "utf8")).tokens).toEqual([]);
  });

  it("exige la clé d'API et ignore un compte inconnu", async () => {
    const { fetchDouble } = microsoftDouble();
    await start(fetchDouble);

    await request(application.getHttpServer())
      .post("/v1/accounts/outlook-perso/connection")
      .expect(401);
    await request(application.getHttpServer())
      .get("/v1/accounts/inconnu/connection")
      .set("X-API-Key", API_KEY)
      .expect(404);
  });
});

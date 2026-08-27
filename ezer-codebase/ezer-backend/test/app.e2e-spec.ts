import { mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { request as httpRequest, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";

import type { INestApplication } from "@nestjs/common";
import request from "supertest";

import { MAX_REQUEST_BODY_BYTES, createEzerApplication } from "../src/bootstrap";

const API_KEY = "test-api-key";

function sendChunkedJson(port: number, body: string): Promise<{ body: string; status: number }> {
  return new Promise((resolve, reject) => {
    const outgoing = httpRequest(
      {
        host: "127.0.0.1",
        port,
        path: "/v1/sync",
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Transfer-Encoding": "chunked",
          "X-API-Key": API_KEY
        }
      },
      (incoming) => {
        const chunks: Buffer[] = [];
        incoming.on("data", (chunk: Buffer) => chunks.push(chunk));
        incoming.once("end", () => {
          resolve({
            body: Buffer.concat(chunks).toString("utf8"),
            status: incoming.statusCode ?? 0
          });
        });
      }
    );
    outgoing.once("error", reject);
    const midpoint = Math.floor(body.length / 2);
    outgoing.write(body.slice(0, midpoint));
    outgoing.write(body.slice(midpoint));
    outgoing.end();
  });
}

describe("Ezer API", () => {
  let application: INestApplication;
  let workingDirectory: string;
  let dataFile: string;

  beforeEach(async () => {
    workingDirectory = await mkdtemp(join(tmpdir(), "ezer-backend-test-"));
    dataFile = join(workingDirectory, "ezer.json");
    process.env.NODE_ENV = "test";
    process.env.EZER_MODE = "demo";
    process.env.EZER_API_KEY = API_KEY;
    process.env.EZER_DATA_FILE = dataFile;
    process.env.EZER_DEMO_RESET_ON_START = "true";
    delete process.env.EZER_SOURCE_FILE;
    delete process.env.EZER_ACCOUNTS_JSON;
    application = await createEzerApplication();
    await application.init();
  });

  afterEach(async () => {
    await application.close();
    await rm(workingDirectory, { recursive: true, force: true });
  });

  it("exposes unauthenticated liveness and readiness checks", async () => {
    const live = await request(application.getHttpServer()).get("/health/live").expect(200);
    expect(live.body).toEqual({ status: "ok" });

    const ready = await request(application.getHttpServer()).get("/health/ready").expect(200);
    expect(ready.body).toEqual({ status: "ready" });
    expect(ready.headers["cache-control"]).toBe("no-store");
    expect(ready.headers["x-request-id"]).toMatch(/^[A-Za-z0-9._-]{1,128}$/);
  });

  it("protects every v1 route with a neutral API-key failure", async () => {
    const response = await request(application.getHttpServer()).get("/v1/accounts").expect(401);
    expect(response.body.message).toBe("unauthorized");
    expect(response.body).not.toHaveProperty("stack");
    expect(response.headers["cache-control"]).toBe("no-store");
  });

  it("returns only public account fields", async () => {
    const response = await request(application.getHttpServer())
      .get("/v1/accounts")
      .set("X-API-Key", API_KEY)
      .set("X-Request-ID", "frontend.request-42")
      .expect(200);

    expect(response.body).toEqual({
      accounts: [
        {
          id: "gmail-primary",
          provider: "gmail",
          mailbox: null,
          status: "disconnected",
          connected_at: null,
          write_enabled: false
        },
        {
          id: "outlook-ops",
          provider: "outlook",
          mailbox: null,
          status: "disconnected",
          connected_at: null,
          write_enabled: false
        }
      ]
    });
    expect(response.headers["x-request-id"]).toBe("frontend.request-42");
  });

  it("paginates, filters and returns analysis details", async () => {
    const page = await request(application.getHttpServer())
      .get("/v1/analyses?limit=2&offset=0")
      .set("X-API-Key", API_KEY)
      .expect(200);

    expect(page.body.limit).toBe(2);
    expect(page.body.offset).toBe(0);
    expect(page.body.total).toBe(4);
    expect(page.body.items).toHaveLength(2);

    const filtered = await request(application.getHttpServer())
      .get("/v1/analyses?category=security&needs_human_review=true")
      .set("X-API-Key", API_KEY)
      .expect(200);
    expect(filtered.body.total).toBe(1);
    expect(filtered.body.items[0].category).toBe("security");

    const analysisId = page.body.items[0].analysis_id as string;
    const detail = await request(application.getHttpServer())
      .get(`/v1/analyses/${analysisId}`)
      .set("X-API-Key", API_KEY)
      .expect(200);
    expect(detail.body.analysis_id).toBe(analysisId);
    expect(detail.body.safety).toBeDefined();
    expect(detail.body.triage).toBeDefined();
  });

  it("rejects unknown fields and malformed filters", async () => {
    const unknownQuery = await request(application.getHttpServer())
      .get("/v1/analyses?unexpected=true")
      .set("X-API-Key", API_KEY)
      .expect(400);
    expect(unknownQuery.body.message).toBe("invalid request");

    await request(application.getHttpServer())
      .get("/v1/analyses?needs_human_review=1")
      .set("X-API-Key", API_KEY)
      .expect(400);

    await request(application.getHttpServer())
      .post("/v1/sync")
      .set("X-API-Key", API_KEY)
      .send({ account_ids: [], extra: true })
      .expect(400);
  });

  it("synchronizes seeded connectors end-to-end and advances cursors", async () => {
    const first = await request(application.getHttpServer())
      .post("/v1/sync")
      .set("X-API-Key", API_KEY)
      .send({})
      .expect(200);

    expect(first.body.reports).toHaveLength(2);
    expect(first.body.reports.reduce((sum: number, report: { processed: number }) => sum + report.processed, 0)).toBe(
      3
    );
    expect(first.body.reports.every((report: { cursor_advanced: boolean }) => report.cursor_advanced)).toBe(
      true
    );

    const page = await request(application.getHttpServer())
      .get("/v1/analyses?limit=100")
      .set("X-API-Key", API_KEY)
      .expect(200);
    expect(page.body.total).toBe(7);

    const second = await request(application.getHttpServer())
      .post("/v1/sync")
      .set("X-API-Key", API_KEY)
      .send({})
      .expect(200);
    expect(second.body.reports.every((report: { fetched: number }) => report.fetched === 0)).toBe(true);

    const persisted = JSON.parse(await readFile(dataFile, "utf8")) as { analyses: unknown[] };
    expect(persisted.analyses).toHaveLength(7);
    expect((await readdir(workingDirectory)).filter((name) => name.endsWith(".tmp"))).toEqual([]);
  });

  it("returns 422 for an unknown synchronization account", async () => {
    const response = await request(application.getHttpServer())
      .post("/v1/sync")
      .set("X-API-Key", API_KEY)
      .send({ account_ids: ["unknown-account"], limit: 10 })
      .expect(422);
    expect(response.body.message).toBe("invalid request");
  });

  it("rejects JSON bodies larger than 64 KiB before controller validation", async () => {
    const response = await request(application.getHttpServer())
      .post("/v1/sync")
      .set("X-API-Key", API_KEY)
      .set("Content-Type", "application/json")
      .send({ padding: "x".repeat(MAX_REQUEST_BODY_BYTES) })
      .expect(413);
    expect(response.body.message).toBe("request too large");
    expect(response.headers["x-request-id"]).toBeDefined();
  });

  it("enforces the body limit on chunked requests and every media type", async () => {
    await application.listen(0, "127.0.0.1");
    const address = (application.getHttpServer() as Server).address() as AddressInfo;

    await request(application.getHttpServer())
      .post("/v1/sync")
      .set("X-API-Key", API_KEY)
      .set("Content-Type", "text/plain")
      .send("x".repeat(MAX_REQUEST_BODY_BYTES + 1))
      .expect(413);

    const chunked = await sendChunkedJson(
      address.port,
      JSON.stringify({ padding: "x".repeat(MAX_REQUEST_BODY_BYTES) })
    );
    expect(chunked.status).toBe(413);
    expect(JSON.parse(chunked.body)).toMatchObject({ message: "request too large" });
  });

  it("accepts JSON only on write routes", async () => {
    const response = await request(application.getHttpServer())
      .post("/v1/sync")
      .set("X-API-Key", API_KEY)
      .set("Content-Type", "text/plain")
      .send("{}")
      .expect(415);
    expect(response.body.message).toBe("unsupported media type");
    expect(response.headers["cache-control"]).toBe("no-store");
  });

  it("replaces invalid incoming request ids", async () => {
    const response = await request(application.getHttpServer())
      .get("/health/live")
      .set("X-Request-ID", "invalid request id with spaces")
      .expect(200);
    expect(response.headers["x-request-id"]).not.toBe("invalid request id with spaces");
    expect(response.headers["x-request-id"]).toMatch(/^[A-Za-z0-9._-]{1,128}$/);
  });
});

import { request as httpRequest } from "node:http";
import type { AddressInfo } from "node:net";

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
        path: "/v1/assistant",
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

describe("Ezer bot API", () => {
  let application: INestApplication;

  beforeEach(async () => {
    process.env.NODE_ENV = "test";
    process.env.EZER_API_KEY = API_KEY;
    delete process.env.EZER_ANTHROPIC_API_KEY;
    delete process.env.EZER_BACKEND_URL;
    delete process.env.EZER_BACKEND_API_KEY;
    application = await createEzerApplication();
    await application.init();
  });

  afterEach(async () => {
    await application.close();
  });

  it("exposes unauthenticated liveness and readiness checks", async () => {
    const live = await request(application.getHttpServer()).get("/health/live").expect(200);
    expect(live.body).toEqual({ status: "ok" });

    const ready = await request(application.getHttpServer()).get("/health/ready").expect(200);
    expect(ready.body).toEqual({ status: "ready" });
    expect(ready.headers["cache-control"]).toBe("no-store");
    expect(ready.headers["x-request-id"]).toMatch(/^[A-Za-z0-9._-]{1,128}$/);
  });

  it("protects the assistant route with the API key", async () => {
    await request(application.getHttpServer())
      .post("/v1/assistant")
      .send({ account_id: "outlook-perso", messages: [{ role: "user", content: "Bonjour" }] })
      .expect(401);
  });

  it("rejects an unconfigured assistant instead of calling a model", async () => {
    const response = await request(application.getHttpServer())
      .post("/v1/assistant")
      .set("X-API-Key", API_KEY)
      .send({ account_id: "outlook-perso", messages: [{ role: "user", content: "Bonjour" }] })
      .expect(422);
    expect(response.body.detail).toBe("invalid request");
    expect(response.headers["cache-control"]).toBe("no-store");
  });

  it("rejects an oversized request before authentication", async () => {
    await request(application.getHttpServer())
      .post("/v1/assistant")
      .send("x".repeat(MAX_REQUEST_BODY_BYTES + 1))
      .expect(413);
  });

  it("bounds a chunked request without Content-Length", async () => {
    await application.listen(0, "127.0.0.1");
    const address = application.getHttpServer().address() as AddressInfo;
    const padding = `"padding":"${"x".repeat(MAX_REQUEST_BODY_BYTES)}"`;
    const result = await sendChunkedJson(address.port, `{${padding}}`);
    expect(result.status).toBe(413);
  });
});

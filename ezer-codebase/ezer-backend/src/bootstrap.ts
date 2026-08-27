import { randomUUID } from "node:crypto";

import { HttpStatus, ValidationPipe, type INestApplication } from "@nestjs/common";
import { NestFactory } from "@nestjs/core";
import type { NextFunction, Request, Response } from "express";

import { AppModule } from "./app.module";
import { NeutralExceptionFilter, type RequestWithId } from "./common/neutral-exception.filter";

export const MAX_REQUEST_BODY_BYTES = 64 * 1024;
const VALID_REQUEST_ID = /^[A-Za-z0-9._-]{1,128}$/;

function requestMetadata(request: RequestWithId, response: Response, next: NextFunction): void {
  const supplied = request.header("X-Request-ID");
  request.requestId = supplied !== undefined && VALID_REQUEST_ID.test(supplied) ? supplied : randomUUID();
  response.setHeader("X-Request-ID", request.requestId);
  response.setHeader("Cache-Control", "no-store");
  response.setHeader("X-Content-Type-Options", "nosniff");
  next();
}

function sendBodyError(request: RequestWithId, response: Response, status: number): void {
  const message =
    status === HttpStatus.PAYLOAD_TOO_LARGE
      ? "request too large"
      : status === HttpStatus.UNSUPPORTED_MEDIA_TYPE
        ? "unsupported media type"
        : "invalid request";
  response.status(status).json({
    statusCode: status,
    message,
    detail: message,
    request_id: request.requestId ?? "unavailable"
  });
}

function boundedRawWriteBody(request: RequestWithId, response: Response, next: NextFunction): void {
  if (!["POST", "PUT", "PATCH"].includes(request.method)) {
    next();
    return;
  }

  const declared = request.headers["content-length"];
  if (
    (declared !== undefined && (Array.isArray(declared) || !/^\d+$/.test(declared))) ||
    (typeof declared === "string" && Number(declared) > MAX_REQUEST_BODY_BYTES)
  ) {
    request.resume();
    sendBodyError(
      request,
      response,
      typeof declared === "string" && /^\d+$/.test(declared)
        ? HttpStatus.PAYLOAD_TOO_LARGE
        : HttpStatus.BAD_REQUEST
    );
    return;
  }

  const chunks: Buffer[] = [];
  let received = 0;
  let exceeded = false;
  let completed = false;

  request.on("data", (chunk: Buffer | string) => {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    received += bytes.length;
    if (received > MAX_REQUEST_BODY_BYTES) {
      exceeded = true;
      chunks.length = 0;
      return;
    }
    if (!exceeded) chunks.push(bytes);
  });
  request.once("end", () => {
    if (completed) return;
    completed = true;
    if (exceeded) {
      sendBodyError(request, response, HttpStatus.PAYLOAD_TOO_LARGE);
      return;
    }
    request.body = Buffer.concat(chunks, received);
    next();
  });
  request.once("error", () => {
    if (completed) return;
    completed = true;
    sendBodyError(request, response, HttpStatus.BAD_REQUEST);
  });
  request.once("aborted", () => {
    completed = true;
  });
}

function decodeJsonWriteBody(request: Request, response: Response, next: NextFunction): void {
  if (!["POST", "PUT", "PATCH"].includes(request.method)) {
    next();
    return;
  }
  const rawBody: unknown = request.body;
  if (!Buffer.isBuffer(rawBody) || rawBody.length === 0) {
    request.body = undefined;
    next();
    return;
  }
  if (!request.is("application/json")) {
    response.status(HttpStatus.UNSUPPORTED_MEDIA_TYPE).json({
      statusCode: HttpStatus.UNSUPPORTED_MEDIA_TYPE,
      message: "unsupported media type",
      detail: "unsupported media type",
      request_id: (request as RequestWithId).requestId ?? "unavailable"
    });
    return;
  }
  try {
    const parsed: unknown = JSON.parse(rawBody.toString("utf8"));
    if (typeof parsed !== "object" || parsed === null) throw new SyntaxError();
    request.body = parsed;
    next();
  } catch {
    response.status(HttpStatus.BAD_REQUEST).json({
      statusCode: HttpStatus.BAD_REQUEST,
      message: "invalid request",
      detail: "invalid request",
      request_id: (request as RequestWithId).requestId ?? "unavailable"
    });
  }
}

export async function createEzerApplication(): Promise<INestApplication> {
  const app = await NestFactory.create(AppModule, {
    bodyParser: false,
    abortOnError: false,
    logger: process.env.NODE_ENV === "test" ? false : undefined
  });
  app.use(requestMetadata);
  // Bound every write request from both Content-Length and the actual streamed bytes.
  app.use(boundedRawWriteBody);
  app.use(decodeJsonWriteBody);
  app.useGlobalPipes(
    new ValidationPipe({
      transform: true,
      whitelist: true,
      forbidNonWhitelisted: true,
      forbidUnknownValues: true,
      stopAtFirstError: true,
      validationError: { target: false, value: false }
    })
  );
  app.useGlobalFilters(app.get(NeutralExceptionFilter));
  return app;
}

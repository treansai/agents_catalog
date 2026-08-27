import { createHash, timingSafeEqual } from "node:crypto";

import { Injectable, UnauthorizedException } from "@nestjs/common";
import type { CanActivate, ExecutionContext } from "@nestjs/common";
import type { Request } from "express";

import { AppConfigService } from "../config/app-config.service";

function digest(value: string): Buffer {
  return createHash("sha256").update(value, "utf8").digest();
}

@Injectable()
export class ApiKeyGuard implements CanActivate {
  constructor(private readonly config: AppConfigService) {}

  canActivate(context: ExecutionContext): boolean {
    const request = context.switchToHttp().getRequest<Request>();
    const supplied = request.header("X-API-Key");
    if (supplied === undefined || !timingSafeEqual(digest(supplied), digest(this.config.apiKey))) {
      throw new UnauthorizedException();
    }
    return true;
  }
}

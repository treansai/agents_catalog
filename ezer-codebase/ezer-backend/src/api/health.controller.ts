import { Controller, Get, Res } from "@nestjs/common";
import type { Response } from "express";

import { JsonPersistenceService } from "../persistence/json-persistence.service";

@Controller("health")
export class HealthController {
  constructor(private readonly persistence: JsonPersistenceService) {}

  @Get("live")
  live(): { status: string } {
    return { status: "ok" };
  }

  @Get("ready")
  async ready(@Res({ passthrough: true }) response: Response): Promise<{ status: string }> {
    if (!(await this.persistence.health())) {
      response.status(503);
      return { status: "unavailable" };
    }
    return { status: "ready" };
  }
}

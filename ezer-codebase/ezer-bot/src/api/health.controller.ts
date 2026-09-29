import { Controller, Get } from "@nestjs/common";

@Controller("health")
export class HealthController {
  @Get("live")
  live(): { status: string } {
    return { status: "ok" };
  }

  @Get("ready")
  ready(): { status: string } {
    return { status: "ready" };
  }
}

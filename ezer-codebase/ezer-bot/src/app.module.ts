import { Module } from "@nestjs/common";

import { HealthController } from "./api/health.controller";
import { V1Controller } from "./api/v1.controller";
import { AssistantService } from "./assistant/assistant.service";
import { ApiKeyGuard } from "./common/api-key.guard";
import { NeutralExceptionFilter } from "./common/neutral-exception.filter";
import { AppConfigService } from "./config/app-config.service";

@Module({
  controllers: [HealthController, V1Controller],
  providers: [AppConfigService, ApiKeyGuard, NeutralExceptionFilter, AssistantService]
})
export class AppModule {}

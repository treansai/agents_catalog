import { Module } from "@nestjs/common";

import { HealthController } from "./api/health.controller";
import { V1Controller } from "./api/v1.controller";
import { AgentUiController } from "./api/agent-ui.controller";
import { ApiKeyGuard } from "./common/api-key.guard";
import { NeutralExceptionFilter } from "./common/neutral-exception.filter";
import { AppConfigService } from "./config/app-config.service";
import {
  HTTP_FETCH_PROVIDER,
  OutlookAuthService,
  SLEEPER_PROVIDER
} from "./mail/outlook-auth.service";
import { GraphMailService } from "./mail/graph-mail.service";
import { TokenStoreService } from "./mail/token-store.service";
import { JsonPersistenceService } from "./persistence/json-persistence.service";
import { ConnectorRegistryService } from "./sync/connector-registry.service";
import { SyncService } from "./sync/sync.service";
import { ActionRegistry } from "./agent-ui/actions";
import { ConfirmationStore } from "./agent-ui/confirmation";
import { DataResolverRegistry } from "./agent-ui/resolvers";
import { IdempotencyStore, ResolverCache } from "./agent-ui/cache";

@Module({
  controllers: [HealthController, V1Controller, AgentUiController],
  providers: [
    AppConfigService,
    JsonPersistenceService,
    TokenStoreService,
    OutlookAuthService,
    HTTP_FETCH_PROVIDER,
    SLEEPER_PROVIDER,
    GraphMailService,
    ConnectorRegistryService,
    SyncService,
    ApiKeyGuard,
    NeutralExceptionFilter,
    ResolverCache,
    IdempotencyStore,
    ConfirmationStore,
    DataResolverRegistry,
    ActionRegistry
  ]
})
export class AppModule {}

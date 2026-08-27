import "reflect-metadata";

import { AppConfigService } from "./config/app-config.service";
import { createEzerApplication } from "./bootstrap";

async function bootstrap(): Promise<void> {
  const app = await createEzerApplication();
  app.enableShutdownHooks();
  const config = app.get(AppConfigService);
  await app.listen(config.port, config.host);
}

void bootstrap();

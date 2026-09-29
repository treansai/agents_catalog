import { Injectable } from "@nestjs/common";

import { HttpAgentUiClient } from "../agent-ui/agent-ui";
import { MailboxAssistant } from "../assistant/assistant";
import { createAnthropicChatModel } from "../assistant/chat-model";
import { AppConfigService } from "../config/app-config.service";
import { HttpMailboxClient } from "../mailbox/mailbox";

@Injectable()
export class AssistantService {
  private assistant: MailboxAssistant | undefined;

  constructor(private readonly config: AppConfigService) {}

  get configured(): boolean {
    return this.config.assistantConfigured;
  }

  instance(): MailboxAssistant {
    if (this.assistant === undefined) {
      const url = this.config.backendUrl;
      const key = this.config.backendApiKey;
      if (url === undefined || key === undefined) {
        throw new Error("assistant backend is not configured");
      }
      this.assistant = new MailboxAssistant(
        this.config,
        new HttpMailboxClient(url, key),
        createAnthropicChatModel,
        new HttpAgentUiClient(url, key)
      );
    }
    return this.assistant;
  }
}

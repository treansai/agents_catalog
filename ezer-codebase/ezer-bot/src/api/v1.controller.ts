import { Body, Controller, Post, UnprocessableEntityException, UseGuards } from "@nestjs/common";

import { ApiKeyGuard } from "../common/api-key.guard";
import { AssistantService } from "../assistant/assistant.service";
import type { AssistantAnswer } from "../assistant/assistant";
import { MailboxUnavailableError } from "../mailbox/mailbox";
import { AssistantRequestDto } from "./dto";

@Controller("v1")
@UseGuards(ApiKeyGuard)
export class V1Controller {
  constructor(private readonly assistant: AssistantService) {}

  @Post("assistant")
  async assistantTurn(@Body() payload: AssistantRequestDto): Promise<AssistantAnswer> {
    if (!this.assistant.configured) {
      throw new UnprocessableEntityException("assistant backend is not configured");
    }
    try {
      return await this.assistant.instance().ask(
        payload.account_id,
        payload.messages,
        payload.approved_deletions,
        payload.ui_action,
        payload.ui_instances
      );
    } catch (error) {
      if (error instanceof MailboxUnavailableError) {
        throw new UnprocessableEntityException(error.code);
      }
      throw error;
    }
  }
}

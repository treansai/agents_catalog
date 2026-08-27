import {
  Body,
  Controller,
  Delete,
  Get,
  HttpCode,
  HttpStatus,
  NotFoundException,
  Param,
  Post,
  Query,
  UnprocessableEntityException,
  UseGuards
} from "@nestjs/common";

import { ApiKeyGuard } from "../common/api-key.guard";
import type {
  Account,
  AccountSummary,
  ConnectionState,
  EmailAnalysis,
  SyncReport
} from "../domain/models";
import {
  GraphMailService,
  type MailboxStats,
  type MessageBody,
  type MessageHeader,
  type SenderTally
} from "../mail/graph-mail.service";
import { ConnectionError, OutlookAuthService } from "../mail/outlook-auth.service";
import { JsonPersistenceService } from "../persistence/json-persistence.service";
import { SyncService } from "../sync/sync.service";
import {
  AccountIdParamDto,
  AnalysisIdParamDto,
  ListAnalysesQueryDto,
  ListMessagesQueryDto,
  MessageIdBodyDto,
  MessageIdQueryDto,
  SenderTallyQueryDto,
  SyncRequestDto
} from "./dto";

/** Réponse d'échec bornée : jamais un message Microsoft, seulement un code stable d'Ezer. */
function failedConnection(account: Account, code: string): ConnectionState {
  return {
    account_id: account.id,
    provider: account.provider,
    mailbox: account.mailbox ?? null,
    status: "failed",
    code,
    verification_uri: null,
    user_code: null,
    expires_at: null,
    connected_at: null,
    write_enabled: false
  };
}

@Controller("v1")
@UseGuards(ApiKeyGuard)
export class V1Controller {
  constructor(
    private readonly persistence: JsonPersistenceService,
    private readonly syncService: SyncService,
    private readonly outlookAuth: OutlookAuthService,
    private readonly graphMail: GraphMailService
  ) {}

  @Get("accounts")
  async accounts(): Promise<{ accounts: AccountSummary[] }> {
    const accounts = await this.persistence.listAccounts();
    return {
      accounts: await Promise.all(
        accounts.map(async (account) => {
          const connection = await this.connectionState(account);
          return {
            id: account.id,
            provider: account.provider,
            mailbox: connection.mailbox,
            status: connection.status,
            connected_at: connection.connected_at,
            write_enabled: connection.write_enabled
          };
        })
      )
    };
  }

  @Get("accounts/:accountId/connection")
  async connection(@Param() parameters: AccountIdParamDto): Promise<ConnectionState> {
    return this.connectionState(await this.requireAccount(parameters.accountId));
  }

  /** Démarre, ou reprend, le flux device code Microsoft pour un compte Outlook configuré. */
  @Post("accounts/:accountId/connection")
  @HttpCode(HttpStatus.OK)
  async connect(@Param() parameters: AccountIdParamDto): Promise<ConnectionState> {
    const account = await this.requireAccount(parameters.accountId);
    try {
      return await this.outlookAuth.connect(account);
    } catch (error) {
      if (error instanceof ConnectionError) return failedConnection(account, error.code);
      throw error;
    }
  }

  @Delete("accounts/:accountId/connection")
  @HttpCode(HttpStatus.OK)
  async disconnect(@Param() parameters: AccountIdParamDto): Promise<ConnectionState> {
    const account = await this.requireAccount(parameters.accountId);
    await this.outlookAuth.disconnect(account);
    return this.connectionState(account);
  }

  @Get("analyses")
  async analyses(@Query() query: ListAnalysesQueryDto): Promise<{
    items: EmailAnalysis[];
    total: number;
    limit: number;
    offset: number;
  }> {
    const page = await this.persistence.listAnalyses(query.limit, query.offset, {
      accountId: query.account_id,
      category: query.category,
      priority: query.priority,
      needsHumanReview: query.needs_human_review
    });
    return { ...page, limit: query.limit, offset: query.offset };
  }

  @Get("analyses/:analysisId")
  async analysis(@Param() parameters: AnalysisIdParamDto): Promise<EmailAnalysis> {
    const analysis = await this.persistence.getAnalysis(parameters.analysisId);
    if (analysis === undefined) throw new NotFoundException();
    return analysis;
  }

  @Post("sync")
  @HttpCode(HttpStatus.OK)
  async sync(@Body() payload?: SyncRequestDto): Promise<{ reports: SyncReport[] }> {
    try {
      return { reports: await this.syncService.sync(payload?.account_ids, payload?.limit) };
    } catch (error) {
      // Une boîte réelle déconnectée est un refus de la demande, pas une panne du service.
      if (error instanceof ConnectionError) throw new UnprocessableEntityException();
      throw error;
    }
  }

  /**
   * Accès direct aux messages d'une boîte connectée. Ces routes sont la seule porte d'entrée des
   * agents d'ezer-bot : aucun credential Microsoft ne quitte ce service.
   */
  @Get("accounts/:accountId/messages")
  async messages(
    @Param() parameters: AccountIdParamDto,
    @Query() query: ListMessagesQueryDto
  ): Promise<{ messages: MessageHeader[] }> {
    const account = await this.requireAccount(parameters.accountId);
    return {
      messages: await this.mailOperation(() =>
        // Graph refuse $search combiné à $filter/$orderby : la recherche exclut donc le tri.
        query.query === undefined
          ? this.graphMail.listMessages(account, {
              top: query.top,
              unreadOnly: query.unread_only,
              fromAddress: query.from_address,
              since: query.since,
              until: query.until,
              order: query.order
            })
          : this.graphMail.search(account, query.query, query.top)
      )
    };
  }

  /** Répartition des messages récents par expéditeur : le tri « qui m'écrit le plus ». */
  @Get("accounts/:accountId/senders")
  async senders(
    @Param() parameters: AccountIdParamDto,
    @Query() query: SenderTallyQueryDto
  ): Promise<{ senders: SenderTally[] }> {
    const account = await this.requireAccount(parameters.accountId);
    return {
      senders: await this.mailOperation(() => this.graphMail.talliesBySender(account, query.sample))
    };
  }

  @Get("accounts/:accountId/message")
  async message(
    @Param() parameters: AccountIdParamDto,
    @Query() query: MessageIdQueryDto
  ): Promise<MessageBody> {
    const account = await this.requireAccount(parameters.accountId);
    return this.mailOperation(() => this.graphMail.getMessage(account, query.message_id));
  }

  @Get("accounts/:accountId/mailbox-stats")
  async mailboxStats(@Param() parameters: AccountIdParamDto): Promise<MailboxStats> {
    const account = await this.requireAccount(parameters.accountId);
    return this.mailOperation(() => this.graphMail.stats(account));
  }

  /** Déplacement vers la corbeille : réversible, et jamais une suppression définitive. */
  @Post("accounts/:accountId/message/trash")
  @HttpCode(HttpStatus.OK)
  async trashMessage(
    @Param() parameters: AccountIdParamDto,
    @Body() payload: MessageIdBodyDto
  ): Promise<{ message_id: string; moved_to: "deleteditems" }> {
    const account = await this.requireAccount(parameters.accountId);
    await this.mailOperation(() =>
      this.graphMail.moveToDeletedItems(account, payload.message_id)
    );
    return { message_id: payload.message_id, moved_to: "deleteditems" };
  }

  private async mailOperation<T>(operation: () => Promise<T>): Promise<T> {
    try {
      return await operation();
    } catch (error) {
      // Une boîte déconnectée ou un consentement manquant est un refus de la demande.
      if (error instanceof ConnectionError) throw new UnprocessableEntityException();
      throw error;
    }
  }

  private async requireAccount(accountId: string): Promise<Account> {
    const account = (await this.persistence.listAccounts()).find(
      (candidate) => candidate.id === accountId
    );
    if (account === undefined) throw new NotFoundException();
    return account;
  }

  private async connectionState(account: Account): Promise<ConnectionState> {
    try {
      return await this.outlookAuth.state(account);
    } catch (error) {
      if (error instanceof ConnectionError) return failedConnection(account, error.code);
      throw error;
    }
  }
}

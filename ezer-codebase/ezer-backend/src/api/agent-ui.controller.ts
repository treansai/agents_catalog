import {
  Body,
  Controller,
  ForbiddenException,
  Get,
  HttpCode,
  HttpException,
  HttpStatus,
  Post,
  Query,
  Req,
  UseGuards
} from "@nestjs/common";

import { ApiKeyGuard } from "../common/api-key.guard";
import type { RequestWithId } from "../common/neutral-exception.filter";
import { ActionRegistry } from "../agent-ui/actions";
import { catalogForPermissions, getComponentDefinition, searchCatalog } from "../agent-ui/catalog";
import { buildPatch, buildRenderSpec } from "../agent-ui/render";
import { AGENT_UI_PROTOCOL_VERSION, parseActionEvent } from "../agent-ui/contracts";
import { AgentUiError, ConfirmationRequiredError } from "../agent-ui/errors";
import { DataResolverRegistry } from "../agent-ui/resolvers";
import { AppConfigService } from "../config/app-config.service";
import { OutlookAuthService } from "../mail/outlook-auth.service";
import { JsonPersistenceService } from "../persistence/json-persistence.service";
import {
  ActionBodyDto,
  CatalogSearchQueryDto,
  PatchBodyDto,
  RenderBodyDto,
  ResolveBodyDto,
  WorkspaceQueryDto
} from "./agent-ui.dto";

@Controller("v1/agent-ui")
@UseGuards(ApiKeyGuard)
export class AgentUiController {
  constructor(
    private readonly persistence: JsonPersistenceService,
    private readonly outlookAuth: OutlookAuthService,
    private readonly resolvers: DataResolverRegistry,
    private readonly actions: ActionRegistry,
    private readonly config: AppConfigService
  ) {}

  @Get("catalog")
  async catalog(
    @Query() query: CatalogSearchQueryDto,
    @Req() request: RequestWithId
  ): Promise<{
    protocolVersion: typeof AGENT_UI_PROTOCOL_VERSION;
    components: ReturnType<typeof catalogForPermissions>;
  }> {
    const session = await this.session(query.workspace_id, request.requestId);
    const filtered =
      query.query === undefined && query.capabilities === undefined && query.limit === undefined;
    return {
      protocolVersion: AGENT_UI_PROTOCOL_VERSION,
      components: filtered
        ? catalogForPermissions(session.permissions)
        : searchCatalog(session.permissions, {
            query: query.query,
            capabilities: query.capabilities,
            limit: query.limit
          })
    };
  }

  /**
   * Valide une proposition d'affichage de l'agent et frappe l'`instanceId`. L'agent n'obtient
   * jamais d'instance qu'il aurait nommée lui-même.
   */
  @Post("render")
  @HttpCode(HttpStatus.OK)
  async render(
    @Query() query: WorkspaceQueryDto,
    @Body() body: RenderBodyDto,
    @Req() request: RequestWithId
  ): Promise<{ status: string; ui: ReturnType<typeof buildRenderSpec> }> {
    const session = await this.session(query.workspace_id, request.requestId);
    try {
      return { status: "success", ui: buildRenderSpec(body, session) };
    } catch (error) {
      this.fail(error);
    }
  }

  @Post("patch")
  @HttpCode(HttpStatus.OK)
  async patch(
    @Query() query: WorkspaceQueryDto,
    @Body() body: PatchBodyDto,
    @Req() request: RequestWithId
  ): Promise<{ status: string; ui: ReturnType<typeof buildPatch> }> {
    const session = await this.session(query.workspace_id, request.requestId);
    try {
      return { status: "success", ui: buildPatch(body, session) };
    } catch (error) {
      this.fail(error);
    }
  }

  @Post("resolve")
  @HttpCode(HttpStatus.OK)
  async resolve(
    @Query() query: WorkspaceQueryDto,
    @Body() body: ResolveBodyDto,
    @Req() request: RequestWithId
  ): Promise<{ status: string; data: unknown }> {
    const session = await this.session(query.workspace_id, request.requestId);
    const component = getComponentDefinition(body.componentId);
    if (component === undefined) {
      this.fail(new AgentUiError("unknown_component", "unknown_component", 400));
    } else if (!component.allowedDataResolvers.includes(body.resolverId)) {
      this.fail(new AgentUiError("unknown_resolver", "unknown_resolver", 400));
    }
    try {
      const data = await this.resolvers.execute(body.resolverId, body.input, session, body.componentId);
      const empty =
        data !== null &&
        typeof data === "object" &&
        "items" in data &&
        Array.isArray((data as { items: unknown }).items) &&
        (data as { items: unknown[] }).items.length === 0;
      return { status: empty ? "empty" : "success", data };
    } catch (error) {
      this.fail(error);
    }
  }

  @Post("action")
  @HttpCode(HttpStatus.OK)
  async action(
    @Query() query: WorkspaceQueryDto,
    @Body() body: ActionBodyDto,
    @Req() request: RequestWithId
  ): Promise<{ status: string; result: unknown; confirmation?: unknown }> {
    const session = await this.session(query.workspace_id, request.requestId);
    try {
      const event = parseActionEvent({ ...body, kind: "ui.action" });
      const result = await this.actions.dispatch(event, session);
      return { status: "success", result };
    } catch (error) {
      if (error instanceof ConfirmationRequiredError) {
        return { status: "confirmation_required", result: null, confirmation: error.confirmation };
      }
      this.fail(error);
    }
  }

  private async session(workspaceId: string, traceId: string | undefined) {
    const accounts = await this.persistence.listAccounts();
    const account = accounts.find((candidate) => candidate.id === workspaceId);
    if (account === undefined) {
      throw new ForbiddenException();
    }
    const permissions = ["mail.read"];
    if (this.config.mode === "demo" || process.env.NODE_ENV === "test") {
      permissions.push("mail.write");
    } else {
      try {
        const state = await this.outlookAuth.state(account);
        if (state.write_enabled) permissions.push("mail.write");
      } catch {
        // Lecture seule si l'état de connexion n'est pas disponible.
      }
    }
    return {
      userId: account.mailbox ?? account.id,
      workspaceId: account.id,
      permissions,
      traceId: traceId ?? "unavailable"
    };
  }

  private fail(error: unknown): never {
    if (error instanceof AgentUiError) {
      throw new HttpException(
        { statusCode: error.status, message: error.code, detail: error.code },
        error.status
      );
    }
    throw error;
  }
}

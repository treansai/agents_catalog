import { Injectable } from "@nestjs/common";

import { GraphMailService } from "../mail/graph-mail.service";
import { ConnectionError } from "../mail/outlook-auth.service";
import { JsonPersistenceService } from "../persistence/json-persistence.service";
import { assertSchema, SchemaValidationError, type JsonSchema } from "./schema";
import type { AgentUiActionEvent, DataResolverContext } from "./contracts";
import { agentUiError, AgentUiError, ConfirmationRequiredError } from "./errors";
import { emitAgentUiEvent } from "./telemetry";
import { ConfirmationStore } from "./confirmation";
import { getComponentDefinition } from "./catalog";
import { IdempotencyStore } from "./cache";

export interface ActionDefinition {
  id: string;
  valuesSchema: JsonSchema;
  requiredPermissions: string[];
  destructive: boolean;
  execute: (
    event: AgentUiActionEvent,
    context: DataResolverContext
  ) => Promise<Record<string, unknown>>;
}

const TARGET_SCHEMA: JsonSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    targetId: { type: "string", maxLength: 512 },
    confirmationId: { type: "string", maxLength: 128 },
    confirmationToken: { type: "string", maxLength: 128 },
    offset: { type: "integer", minimum: 0, maximum: 1_000_000 },
    limit: { type: "integer", minimum: 1, maximum: 25 },
    query: { type: "string", maxLength: 200 },
    optionId: { type: "string", maxLength: 64 }
  }
};

@Injectable()
export class ActionRegistry {
  private readonly actions: Map<string, ActionDefinition>;

  constructor(
    private readonly graph: GraphMailService,
    private readonly persistence: JsonPersistenceService,
    private readonly confirmations: ConfirmationStore,
    private readonly idempotency: IdempotencyStore
  ) {
    this.actions = new Map(
      [
        this.openMessage(),
        this.openInvoice(),
        this.pageTable(),
        this.filterTable(),
        this.draftReply(),
        this.trashMessage(),
        this.confirm(),
        this.cancel()
      ].map((action) => [action.id, action])
    );
  }

  get(actionId: string): ActionDefinition | undefined {
    return this.actions.get(actionId);
  }

  async dispatch(
    event: AgentUiActionEvent,
    context: DataResolverContext
  ): Promise<Record<string, unknown>> {
    emitAgentUiEvent({
      event: "ui_action_received",
      traceId: context.traceId,
      messageId: event.messageId,
      instanceId: event.instanceId,
      componentId: event.componentId,
      componentVersion: event.componentVersion,
      actionId: event.actionId,
      status: "ok"
    });

    const component = getComponentDefinition(event.componentId);
    if (component === undefined) {
      emitAgentUiEvent({
        event: "ui_action_rejected",
        traceId: context.traceId,
        actionId: event.actionId,
        componentId: event.componentId,
        status: "denied",
        code: "unknown_component"
      });
      throw agentUiError("unknown_component", 400);
    }
    if (!component.allowedActions.includes(event.actionId)) {
      emitAgentUiEvent({
        event: "ui_action_rejected",
        traceId: context.traceId,
        actionId: event.actionId,
        componentId: event.componentId,
        status: "denied",
        code: "unknown_action"
      });
      throw agentUiError("unknown_action", 400);
    }

    const action = this.actions.get(event.actionId);
    if (action === undefined) throw agentUiError("unknown_action", 400);
    if (!action.requiredPermissions.every((permission) => context.permissions.includes(permission))) {
      emitAgentUiEvent({
        event: "ui_action_rejected",
        traceId: context.traceId,
        actionId: event.actionId,
        status: "denied",
        code: "permission_denied"
      });
      throw agentUiError("permission_denied", 403);
    }
    try {
      assertSchema(event.values, action.valuesSchema);
    } catch (error) {
      if (error instanceof SchemaValidationError) throw agentUiError("invalid_payload", 400);
      throw error;
    }

    const started = Date.now();
    try {
      const result = await this.idempotency.remember(
        `${context.workspaceId}:${event.idempotencyKey}`,
        () => action.execute(event, context)
      );
      emitAgentUiEvent({
        event: "ui_action_succeeded",
        traceId: context.traceId,
        actionId: event.actionId,
        componentId: event.componentId,
        durationMs: Date.now() - started,
        status: "ok"
      });
      return result;
    } catch (error) {
      if (error instanceof AgentUiError) {
        emitAgentUiEvent({
          event: "ui_action_rejected",
          traceId: context.traceId,
          actionId: event.actionId,
          status: "denied",
          code: error.code
        });
      }
      throw error;
    }
  }

  private openMessage(): ActionDefinition {
    return {
      id: "messages.open",
      valuesSchema: TARGET_SCHEMA,
      requiredPermissions: ["mail.read"],
      destructive: false,
      execute: async (event) => ({ kind: "open", targetId: event.values.targetId ?? null })
    };
  }

  private openInvoice(): ActionDefinition {
    return {
      id: "invoices.open",
      valuesSchema: TARGET_SCHEMA,
      requiredPermissions: ["mail.read"],
      destructive: false,
      execute: async (event) => ({ kind: "open", targetId: event.values.targetId ?? null })
    };
  }

  private pageTable(): ActionDefinition {
    return {
      id: "table.page",
      valuesSchema: TARGET_SCHEMA,
      requiredPermissions: ["mail.read"],
      destructive: false,
      execute: async (event) => ({
        kind: "page",
        offset: event.values.offset ?? 0,
        limit: event.values.limit ?? 20
      })
    };
  }

  private filterTable(): ActionDefinition {
    return {
      id: "table.filter",
      valuesSchema: TARGET_SCHEMA,
      requiredPermissions: ["mail.read"],
      destructive: false,
      execute: async (event) => ({ kind: "filter", optionId: event.values.optionId ?? null })
    };
  }

  private draftReply(): ActionDefinition {
    return {
      id: "draft.reply",
      valuesSchema: TARGET_SCHEMA,
      requiredPermissions: ["mail.read"],
      destructive: false,
      execute: async (event) => ({ kind: "draft_requested", targetId: event.values.targetId ?? null })
    };
  }

  private trashMessage(): ActionDefinition {
    return {
      id: "messages.trash",
      valuesSchema: TARGET_SCHEMA,
      requiredPermissions: ["mail.write"],
      destructive: true,
      execute: async (event, context) => {
        const targetId = String(event.values.targetId ?? "");
        if (targetId.length === 0) throw agentUiError("invalid_payload", 400);
        const confirmationId = event.values.confirmationId;
        const confirmationToken = event.values.confirmationToken;
        if (typeof confirmationId !== "string" || typeof confirmationToken !== "string") {
          throw this.challengeTrash(context.workspaceId, targetId);
        }
        const consumed = this.confirmations.consume(
          confirmationId,
          confirmationToken,
          context.workspaceId,
          "messages.trash"
        );
        if (consumed === undefined || consumed.targetId !== targetId) {
          throw agentUiError("confirmation_invalid", 409);
        }
        return this.runTrash(context.workspaceId, targetId);
      }
    };
  }

  private confirm(): ActionDefinition {
    return {
      id: "confirmation.confirm",
      valuesSchema: TARGET_SCHEMA,
      requiredPermissions: ["mail.read"],
      destructive: false,
      execute: async (event, context) => {
        const confirmationId = String(event.values.confirmationId ?? "");
        const confirmationToken = String(event.values.confirmationToken ?? "");
        const peeked = this.confirmations.peek(confirmationId, context.workspaceId);
        if (peeked === undefined) throw agentUiError("confirmation_invalid", 409);
        const stored = this.actions.get(peeked.actionId);
        if (stored === undefined) throw agentUiError("unknown_action", 400);
        if (!stored.requiredPermissions.every((permission) => context.permissions.includes(permission))) {
          throw agentUiError("permission_denied", 403);
        }
        const consumed = this.confirmations.consume(
          confirmationId,
          confirmationToken,
          context.workspaceId,
          peeked.actionId
        );
        if (consumed === undefined) throw agentUiError("confirmation_invalid", 409);
        if (consumed.actionId === "messages.trash") {
          return this.runTrash(context.workspaceId, consumed.targetId);
        }
        throw agentUiError("unknown_action", 400);
      }
    };
  }

  private cancel(): ActionDefinition {
    return {
      id: "confirmation.cancel",
      valuesSchema: TARGET_SCHEMA,
      requiredPermissions: ["mail.read"],
      destructive: false,
      execute: async () => ({ kind: "cancelled" })
    };
  }

  private challengeTrash(workspaceId: string, targetId: string): ConfirmationRequiredError {
    const record = this.confirmations.issue({
      workspaceId,
      actionId: "messages.trash",
      targetId,
      targetLabel: targetId,
      impact: "Le message sera déplacé vers la corbeille.",
      reversible: true
    });
    return new ConfirmationRequiredError({
      confirmationId: record.id,
      action: "mettre à la corbeille",
      target: record.targetLabel,
      impact: record.impact,
      reversible: true,
      token: record.token
    });
  }

  private async runTrash(workspaceId: string, targetId: string): Promise<Record<string, unknown>> {
    const accounts = await this.persistence.listAccounts();
    const account = accounts.find((candidate) => candidate.id === workspaceId);
    if (account === undefined) throw agentUiError("workspace_mismatch", 403);
    try {
      await this.graph.moveToDeletedItems(account, targetId);
    } catch (error) {
      if (error instanceof ConnectionError) throw agentUiError("resolver_failed", 422);
      throw error;
    }
    return { kind: "trashed", targetId, reversible: true };
  }
}

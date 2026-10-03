"""Actions déclaratives déclenchées depuis l'interface : jamais de code, uniquement des identifiants."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.agent_ui.cache import IdempotencyStore
from app.agent_ui.catalog import get_component_definition
from app.agent_ui.confirmation import ConfirmationStore
from app.agent_ui.contracts import AgentUiActionEvent, DataResolverContext
from app.agent_ui.errors import (
    AgentUiError,
    ConfirmationChallenge,
    ConfirmationRequiredError,
    agent_ui_error,
)
from app.agent_ui.schema import JsonSchema, SchemaValidationError, assert_schema
from app.agent_ui.telemetry import emit_agent_ui_event
from app.domain.models import Account
from app.mail.errors import MailConnectionError
from app.mail.graph_mail import GraphMail
from app.persistence.json_store import JsonPersistence

TARGET_SCHEMA: JsonSchema = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "targetId": {"type": "string", "maxLength": 512},
        "confirmationId": {"type": "string", "maxLength": 128},
        "confirmationToken": {"type": "string", "maxLength": 128},
        "offset": {"type": "integer", "minimum": 0, "maximum": 1_000_000},
        "limit": {"type": "integer", "minimum": 1, "maximum": 25},
        "query": {"type": "string", "maxLength": 200},
        "optionId": {"type": "string", "maxLength": 64},
    },
}


@dataclass
class ActionDefinition:
    id: str
    values_schema: JsonSchema
    required_permissions: list[str]
    destructive: bool
    execute: Callable[[AgentUiActionEvent, DataResolverContext], Awaitable[dict[str, Any]]]


class ActionRegistry:
    def __init__(
        self,
        graph: GraphMail,
        persistence: JsonPersistence,
        confirmations: ConfirmationStore,
        idempotency: IdempotencyStore,
    ) -> None:
        self._graph = graph
        self._persistence = persistence
        self._confirmations = confirmations
        self._idempotency = idempotency
        definitions = [
            self._simple("messages.open", lambda e: {"kind": "open", "targetId": e.values.get("targetId")}),
            self._simple("invoices.open", lambda e: {"kind": "open", "targetId": e.values.get("targetId")}),
            self._simple(
                "table.page",
                lambda e: {
                    "kind": "page",
                    "offset": e.values.get("offset", 0),
                    "limit": e.values.get("limit", 20),
                },
            ),
            self._simple("table.filter", lambda e: {"kind": "filter", "optionId": e.values.get("optionId")}),
            self._simple("draft.reply", lambda e: {"kind": "draft_requested", "targetId": e.values.get("targetId")}),
            self._trash_message(),
            self._confirm(),
            self._simple("confirmation.cancel", lambda _e: {"kind": "cancelled"}),
        ]
        self._actions = {definition.id: definition for definition in definitions}

    def get(self, action_id: str) -> ActionDefinition | None:
        return self._actions.get(action_id)

    async def dispatch(self, event: AgentUiActionEvent, context: DataResolverContext) -> dict[str, Any]:
        emit_agent_ui_event(
            event="ui_action_received",
            traceId=context.trace_id,
            messageId=event.messageId,
            instanceId=event.instanceId,
            componentId=event.componentId,
            componentVersion=event.componentVersion,
            actionId=event.actionId,
            status="ok",
        )

        component = get_component_definition(event.componentId)
        if component is None:
            emit_agent_ui_event(
                event="ui_action_rejected",
                traceId=context.trace_id,
                actionId=event.actionId,
                componentId=event.componentId,
                status="denied",
                code="unknown_component",
            )
            raise agent_ui_error("unknown_component", 400)
        if event.actionId not in component["allowedActions"]:
            emit_agent_ui_event(
                event="ui_action_rejected",
                traceId=context.trace_id,
                actionId=event.actionId,
                componentId=event.componentId,
                status="denied",
                code="unknown_action",
            )
            raise agent_ui_error("unknown_action", 400)

        action = self._actions.get(event.actionId)
        if action is None:
            raise agent_ui_error("unknown_action", 400)
        if not all(permission in context.permissions for permission in action.required_permissions):
            emit_agent_ui_event(
                event="ui_action_rejected",
                traceId=context.trace_id,
                actionId=event.actionId,
                status="denied",
                code="permission_denied",
            )
            raise agent_ui_error("permission_denied", 403)
        try:
            assert_schema(event.values, action.values_schema)
        except SchemaValidationError as error:
            raise agent_ui_error("invalid_payload", 400) from error

        started = time.monotonic()
        try:
            result = await self._idempotency.remember(
                f"{context.workspace_id}:{event.idempotencyKey}",
                lambda: action.execute(event, context),
            )
            emit_agent_ui_event(
                event="ui_action_succeeded",
                traceId=context.trace_id,
                actionId=event.actionId,
                componentId=event.componentId,
                durationMs=round((time.monotonic() - started) * 1000),
                status="ok",
            )
            return result
        except AgentUiError as error:
            emit_agent_ui_event(
                event="ui_action_rejected",
                traceId=context.trace_id,
                actionId=event.actionId,
                status="denied",
                code=error.code,
            )
            raise

    @staticmethod
    def _simple(action_id: str, build: Callable[[AgentUiActionEvent], dict[str, Any]]) -> ActionDefinition:
        async def execute(event: AgentUiActionEvent, _context: DataResolverContext) -> dict[str, Any]:
            return build(event)

        return ActionDefinition(action_id, TARGET_SCHEMA, ["mail.read"], False, execute)

    def _trash_message(self) -> ActionDefinition:
        async def execute(event: AgentUiActionEvent, context: DataResolverContext) -> dict[str, Any]:
            target = event.values.get("targetId")
            target_id = "" if target is None else str(target)
            if len(target_id) == 0:
                raise agent_ui_error("invalid_payload", 400)
            confirmation_id = event.values.get("confirmationId")
            confirmation_token = event.values.get("confirmationToken")
            if not isinstance(confirmation_id, str) or not isinstance(confirmation_token, str):
                raise self._challenge_trash(context.workspace_id, target_id)
            consumed = self._confirmations.consume(
                confirmation_id, confirmation_token, context.workspace_id, "messages.trash"
            )
            if consumed is None or consumed.target_id != target_id:
                raise agent_ui_error("confirmation_invalid", 409)
            return await self._run_trash(context.workspace_id, target_id)

        return ActionDefinition("messages.trash", TARGET_SCHEMA, ["mail.write"], True, execute)

    def _confirm(self) -> ActionDefinition:
        async def execute(event: AgentUiActionEvent, context: DataResolverContext) -> dict[str, Any]:
            confirmation_id = str(event.values.get("confirmationId") or "")
            confirmation_token = str(event.values.get("confirmationToken") or "")
            peeked = self._confirmations.peek(confirmation_id, context.workspace_id)
            if peeked is None:
                raise agent_ui_error("confirmation_invalid", 409)
            stored = self._actions.get(peeked.action_id)
            if stored is None:
                raise agent_ui_error("unknown_action", 400)
            if not all(permission in context.permissions for permission in stored.required_permissions):
                raise agent_ui_error("permission_denied", 403)
            consumed = self._confirmations.consume(
                confirmation_id, confirmation_token, context.workspace_id, peeked.action_id
            )
            if consumed is None:
                raise agent_ui_error("confirmation_invalid", 409)
            if consumed.action_id == "messages.trash":
                return await self._run_trash(context.workspace_id, consumed.target_id)
            raise agent_ui_error("unknown_action", 400)

        return ActionDefinition("confirmation.confirm", TARGET_SCHEMA, ["mail.read"], False, execute)

    def _challenge_trash(self, workspace_id: str, target_id: str) -> ConfirmationRequiredError:
        record = self._confirmations.issue(
            workspace_id=workspace_id,
            action_id="messages.trash",
            target_id=target_id,
            target_label=target_id,
            impact="Le message sera déplacé vers la corbeille.",
            reversible=True,
        )
        return ConfirmationRequiredError(
            ConfirmationChallenge(
                confirmationId=record.id,
                action="mettre à la corbeille",
                target=record.target_label,
                impact=record.impact,
                reversible=True,
                token=record.token,
            )
        )

    async def _run_trash(self, workspace_id: str, target_id: str) -> dict[str, Any]:
        account = next(
            (
                Account.model_validate(raw)
                for raw in await self._persistence.list_accounts()
                if raw["id"] == workspace_id
            ),
            None,
        )
        if account is None:
            raise agent_ui_error("workspace_mismatch", 403)
        try:
            await self._graph.move_to_deleted_items(account, target_id)
        except MailConnectionError as error:
            raise agent_ui_error("resolver_failed", 422) from error
        return {"kind": "trashed", "targetId": target_id, "reversible": True}

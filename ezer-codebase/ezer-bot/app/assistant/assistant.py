"""Multi-agent mailbox assistant: orchestrator with tools, plus two tool-less sub-agents."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypedDict

from app.agent_ui.agent_ui import (
    MAX_UI_MESSAGES,
    PROTOCOL_VERSION,
    AgentUiClient,
    AgentUiError,
    CatalogComponent,
    UiActionEvent,
    UiInstanceLedger,
    UiInstanceRef,
    catalog_prompt_payload,
    new_message_id,
    strip_session_keys,
)
from app.assistant.chat_model import (
    ChatModel,
    ChatModelFactory,
    ChatTurn,
    ToolCall,
    create_anthropic_chat_model,
)
from app.assistant.prompts import (
    ANALYST_SYSTEM_PROMPT,
    ASSISTANT_PROMPT_VERSION,
    ORCHESTRATOR_SYSTEM_PROMPT,
    SUMMARIZER_SYSTEM_PROMPT,
)
from app.assistant.tools import ALL_TOOL_SCHEMAS
from app.mailbox.mailbox import (
    MailboxClient,
    MailboxStats,
    MailboxUnavailableError,
    MessageBody,
    MessageHeader,
    SenderTally,
    TriagedAnalysis,
    as_integer,
    as_record,
)
from app.security import UNTRUSTED_EMAIL_POLICY

__all__ = [
    "ALL_TOOL_SCHEMAS",
    "ANALYST_SYSTEM_PROMPT",
    "ASSISTANT_PROMPT_VERSION",
    "SUMMARIZER_SYSTEM_PROMPT",
    "AssistantAnswer",
    "AssistantTurn",
    "MailboxAssistant",
]

MAX_TURNS = 8
MAX_REPLY_CHARS = 8_000
DEFAULT_TOP = 10

AssistantView = dict[str, Any]
UiMessage = dict[str, Any]


class AssistantTurn(TypedDict):
    role: str
    content: str


@dataclass
class AssistantAnswer:
    reply: str
    pending_deletions: list[dict[str, str]]
    deleted: list[dict[str, str]]
    views: list[AssistantView]
    ui_messages: list[UiMessage]
    tools_used: list[str]
    model_id: str
    prompt_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "pending_deletions": self.pending_deletions,
            "deleted": self.deleted,
            "views": self.views,
            "ui_messages": self.ui_messages,
            "tools_used": self.tools_used,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
        }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def header_line(header: MessageHeader) -> str:
    flags = ["lu" if header.is_read else "non lu"]
    if header.has_attachments:
        flags.append("pièce jointe")
    return "\n".join(
        [
            f"id: {header.message_id}",
            f"de: {header.sender_name} <{header.sender_address}>",
            f"objet: {header.subject}",
            f"reçu: {header.received_at}",
            f"état: {', '.join(flags)}",
            f"extrait: {header.snippet}",
        ]
    )


def header_view(header: MessageHeader) -> dict[str, Any]:
    return {
        "message_id": header.message_id,
        "subject": header.subject,
        "sender_name": header.sender_name,
        "sender_address": header.sender_address,
        "received_at": header.received_at,
        "is_read": header.is_read,
        "has_attachments": header.has_attachments,
        "snippet": header.snippet,
    }


def message_view(message: MessageBody) -> AssistantView:
    return {"kind": "message", "message": header_view(message.header), "body_text": message.body_text}


def stats_view(stats: MailboxStats) -> AssistantView:
    return {
        "kind": "stats",
        "mailbox": stats.mailbox,
        "total_messages": stats.total_messages,
        "unread_messages": stats.unread_messages,
        "folders": [
            {"name": name, "total": total, "unread": unread} for name, total, unread in stats.folders
        ],
    }


def triage_view(analyses: list[TriagedAnalysis], total: int) -> AssistantView:
    return {
        "kind": "triage",
        "total": total,
        "items": [
            {
                "summary": item.summary,
                "category": item.category,
                "priority": item.priority,
                "needs_human_review": item.needs_human_review,
                "created_at": item.created_at,
            }
            for item in analyses
        ],
    }


def sender_line(tally: SenderTally) -> str:
    return (
        f"{tally.sender_name} <{tally.sender_address}> — {tally.total} message(s), {tally.unread} non lu(s)"
    )


def optional_str(arguments: dict[str, Any], field_name: str) -> str | None:
    value = arguments.get(field_name)
    if isinstance(value, str) and value.strip() != "":
        return value.strip()
    return None


def filter_title(arguments: dict[str, Any]) -> str:
    parts: list[str] = []
    if arguments.get("unread_only") is True:
        parts.append("non lus")
    sender = optional_str(arguments, "from_address")
    if sender is not None:
        parts.append(f"de {sender}")
    since = optional_str(arguments, "since")
    if since is not None:
        parts.append(f"depuis {since}")
    until = optional_str(arguments, "until")
    if until is not None:
        parts.append(f"jusqu'au {until}")
    return f"Messages {' · '.join(parts) if parts else 'récents'}"


def as_untrusted(label: str, content: str) -> str:
    return "\n".join(
        [
            UNTRUSTED_EMAIL_POLICY,
            f"--- BEGIN UNTRUSTED MAILBOX DATA ({label}) ---",
            content,
            "--- END UNTRUSTED MAILBOX DATA ---",
        ]
    )


def top_of(arguments: dict[str, Any]) -> int:
    value = as_integer(arguments.get("top"))
    if value is None:
        return DEFAULT_TOP
    return max(1, min(value, 25))


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def data_source(value: Any) -> dict[str, Any] | None:
    record = as_record(value)
    if record is None:
        return None
    mode = record.get("mode")
    if mode == "inline":
        source: dict[str, Any] = {"mode": "inline"}
        if "value" in record:
            source["value"] = record["value"]
        return source
    if mode == "resolver":
        resolver_id = record.get("resolver_id")
        if resolver_id is None:
            resolver_id = record.get("resolverId")
        if not isinstance(resolver_id, str):
            return None
        payload = as_record(record.get("input"))
        return {
            "mode": "resolver",
            "resolverId": resolver_id,
            "input": strip_session_keys(payload) if payload is not None else {},
        }
    return None


UI_FAILURE_GUIDANCE: dict[str, str] = {
    "unknown_component": (
        "Ce composant n'existe pas. Consulte get_ui_component_catalog et choisis un identifiant "
        "réel ; n'en invente pas un autre."
    ),
    "invalid_props": (
        "Props refusées par le schéma. Corrige-les une seule fois d'après le schéma du catalogue, "
        "sinon réponds en texte."
    ),
    "unknown_resolver": (
        "Résolveur non autorisé pour ce composant. Utilise un résolveur listé, ou explique que la "
        "donnée n'est pas accessible."
    ),
    "permission_denied": (
        "Accès refusé pour cette session. Informe l'opérateur sans détail technique et n'essaie "
        "pas un autre outil pour contourner."
    ),
    "payload_too_large": (
        "Charge trop volumineuse. Utilise un résolveur paginé plutôt que des données inline."
    ),
    "component_version_mismatch": (
        "Version obsolète. Relis le catalogue et reconstruis le payload avec la version courante."
    ),
}


def ui_failure(error: AgentUiError) -> str:
    guidance = UI_FAILURE_GUIDANCE.get(
        error.code, "Affichage impossible : donne une réponse textuelle utile, sans réessayer."
    )
    return f"Affichage refusé (code {error.code}). {guidance}"


def conversation(history: list[AssistantTurn]) -> list[ChatTurn]:
    messages: list[ChatTurn] = []
    role = ""
    for turn in history:
        if turn["role"] == role and messages:
            previous = messages[-1]
            previous.content = f"{previous.content}\n{turn['content']}"
            continue
        role = turn["role"]
        messages.append(ChatTurn(role=turn["role"], content=turn["content"]))
    return messages


@dataclass
class TurnState:
    account_id: str
    approved_deletions: frozenset[str]
    pending: list[dict[str, str]] = field(default_factory=list)
    deleted: list[dict[str, str]] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    views: list[AssistantView] = field(default_factory=list)
    seen: dict[str, MessageHeader] = field(default_factory=dict)
    ui_messages: list[UiMessage] = field(default_factory=list)
    ledger: UiInstanceLedger = field(default_factory=UiInstanceLedger)
    catalog: dict[str, CatalogComponent] = field(default_factory=dict)

    def emit(self, message: UiMessage) -> bool:
        if len(self.ui_messages) >= MAX_UI_MESSAGES:
            return False
        self.ui_messages.append(message)
        return True

    def show(self, view: AssistantView) -> None:
        self.views = [existing for existing in self.views if existing["kind"] != view["kind"]]
        self.views.append(view)

    def remember(self, headers: list[MessageHeader]) -> None:
        for header in headers:
            self.seen[header.message_id] = header

    def target(self, message_id: str) -> dict[str, str]:
        header = self.seen.get(message_id)
        return {
            "message_id": message_id,
            "subject": header.subject if header else "",
            "sender_address": header.sender_address if header else "",
            "received_at": header.received_at if header else "",
        }


ToolHandler = Callable[[dict[str, Any], TurnState], Awaitable[str]]


class MailboxAssistant:
    def __init__(
        self,
        config: Any,
        mailbox: MailboxClient,
        model_factory: ChatModelFactory = create_anthropic_chat_model,
        ui: AgentUiClient | None = None,
    ) -> None:
        self._mailbox = mailbox
        self._ui = ui
        self._orchestrator: ChatModel = model_factory(config, tools=True)
        self._sub_agent: ChatModel = model_factory(config, tools=False)
        self._model_id: str = config.anthropic_model

    async def ask(
        self,
        account_id: str,
        history: list[AssistantTurn],
        approved_deletions: list[str] | None = None,
        ui_action: UiActionEvent | None = None,
        ui_instances: list[UiInstanceRef] | None = None,
    ) -> AssistantAnswer:
        state = TurnState(account_id, frozenset(approved_deletions or []))
        for instance in (ui_instances or [])[:24]:
            state.ledger.declare(instance.instance_id, instance.component_id, instance.component_version)
        messages: list[ChatTurn] = [
            ChatTurn(role="system", content=ORCHESTRATOR_SYSTEM_PROMPT),
            *conversation(history),
        ]
        if ui_action is not None:
            framed = await self._frame_action(ui_action, state)
            if framed is None:
                return AssistantAnswer(
                    reply="Cette action n'est pas autorisée pour ce composant.",
                    pending_deletions=[],
                    deleted=[],
                    views=[],
                    ui_messages=[],
                    tools_used=[],
                    model_id=self._model_id,
                    prompt_version=ASSISTANT_PROMPT_VERSION,
                )
            messages.append(ChatTurn(role="user", content=framed))

        reply = ""
        for _ in range(MAX_TURNS):
            response = await self._orchestrator.invoke(messages)
            messages.append(response)
            tool_calls = response.tool_calls or []
            if not tool_calls:
                reply = response.content
                break
            for call in tool_calls[:8]:
                messages.append(
                    ChatTurn(role="tool", content=await self._run_tool(call, state), tool_call_id=call.id)
                )

        return AssistantAnswer(
            reply=reply[:MAX_REPLY_CHARS],
            pending_deletions=state.pending,
            deleted=state.deleted,
            views=state.views[-4:],
            ui_messages=state.ui_messages,
            tools_used=state.tools_used,
            model_id=self._model_id,
            prompt_version=ASSISTANT_PROMPT_VERSION,
        )

    async def _run_tool(self, call: ToolCall, state: TurnState) -> str:
        handler = self._handlers().get(call.name)
        if handler is None:
            return "Outil inconnu."
        state.tools_used.append(call.name)
        try:
            return await handler(call.args, state)
        except MailboxUnavailableError as error:
            return f"L'outil a échoué (code {error.code})."

    def _handlers(self) -> dict[str, ToolHandler]:
        return {
            "list_recent_messages": self._list_recent,
            "list_messages": self._list_messages,
            "list_senders": self._list_senders,
            "list_triaged": self._list_triaged,
            "search_messages": self._search,
            "read_message": self._read,
            "summarize_mailbox": self._summarize,
            "analyze_message": self._analyze,
            "request_delete_message": self._request_delete,
            "get_ui_component_catalog": self._ui_catalog,
            "render_ui_component": self._ui_render,
            "update_ui_component": self._ui_update,
            "remove_ui_component": self._ui_remove,
        }

    async def _list_recent(self, arguments: dict[str, Any], state: TurnState) -> str:
        headers = await self._mailbox.list_recent(state.account_id, top_of(arguments))
        state.remember(headers)
        state.show(
            {"kind": "messages", "title": "Messages récents", "items": [header_view(h) for h in headers]}
        )
        if not headers:
            return "Aucun message dans la boîte de réception."
        return as_untrusted("en-têtes", "\n---\n".join(header_line(h) for h in headers))

    async def _list_messages(self, arguments: dict[str, Any], state: TurnState) -> str:
        order = arguments.get("order")
        headers = await self._mailbox.list_messages(
            state.account_id,
            top=top_of(arguments),
            unread_only=arguments.get("unread_only") is True,
            from_address=optional_str(arguments, "from_address"),
            since=optional_str(arguments, "since"),
            until=optional_str(arguments, "until"),
            order="asc" if order == "asc" else "desc",
        )
        state.remember(headers)
        state.show(
            {"kind": "messages", "title": filter_title(arguments), "items": [header_view(h) for h in headers]}
        )
        if not headers:
            return "Aucun message ne correspond à ces critères."
        return as_untrusted("en-têtes", "\n---\n".join(header_line(h) for h in headers))

    async def _list_senders(self, arguments: dict[str, Any], state: TurnState) -> str:
        sample = as_integer(arguments.get("sample"))
        tallies = await self._mailbox.senders(state.account_id, sample if sample is not None else 25)
        state.show(
            {
                "kind": "senders",
                "items": [
                    {
                        "sender_name": tally.sender_name,
                        "sender_address": tally.sender_address,
                        "total": tally.total,
                        "unread": tally.unread,
                    }
                    for tally in tallies
                ],
            }
        )
        if not tallies:
            return "Aucun expéditeur récent."
        return as_untrusted("expéditeurs", "\n".join(sender_line(t) for t in tallies))

    async def _list_triaged(self, arguments: dict[str, Any], state: TurnState) -> str:
        limit = as_integer(arguments.get("limit"))
        needs_review = arguments.get("needs_human_review")
        analyses, total = await self._mailbox.analyses(
            state.account_id,
            limit=limit if limit is not None else 20,
            category=optional_str(arguments, "category"),
            priority=optional_str(arguments, "priority"),
            needs_human_review=needs_review if isinstance(needs_review, bool) else None,
        )
        state.show(triage_view(analyses, total))
        if not analyses:
            return "Aucune analyse ne correspond à ces critères."
        lines = []
        for item in analyses:
            review = ", à relire" if item.needs_human_review else ""
            lines.append(f"[{item.priority}/{item.category}{review}] {item.summary}")
        return as_untrusted("analyses", f"{total} au total.\n" + "\n".join(lines))

    async def _search(self, arguments: dict[str, Any], state: TurnState) -> str:
        query = arguments.get("query")
        if not isinstance(query, str):
            return "Terme de recherche manquant."
        headers = await self._mailbox.search(state.account_id, query, top_of(arguments))
        state.remember(headers)
        state.show(
            {
                "kind": "messages",
                "title": f"Recherche : {query[:80]}",
                "items": [header_view(h) for h in headers],
            }
        )
        if not headers:
            return "Aucun message ne correspond à cette recherche."
        return as_untrusted("en-têtes", "\n---\n".join(header_line(h) for h in headers))

    async def _read(self, arguments: dict[str, Any], state: TurnState) -> str:
        message_id = arguments.get("message_id")
        if not isinstance(message_id, str):
            return "Identifiant de message manquant."
        message = await self._mailbox.get_message(state.account_id, message_id)
        state.remember([message.header])
        state.show(message_view(message))
        return as_untrusted("message", f"{header_line(message.header)}\ncorps:\n{message.body_text}")

    async def _summarize(self, _arguments: dict[str, Any], state: TurnState) -> str:
        stats = await self._mailbox.stats(state.account_id)
        headers = await self._mailbox.list_recent(state.account_id, 15)
        state.remember(headers)
        state.show(stats_view(stats))
        folders = ", ".join(f"{name} ({total}, {unread} non lus)" for name, total, unread in stats.folders)
        briefing = "\n".join(
            [
                f"Boîte : {stats.mailbox}",
                f"Total : {stats.total_messages}, non lus : {stats.unread_messages}",
                f"Dossiers : {folders}",
                "",
                as_untrusted("en-têtes", "\n---\n".join(header_line(h) for h in headers)),
            ]
        )
        return await self._delegate(SUMMARIZER_SYSTEM_PROMPT, briefing)

    async def _analyze(self, arguments: dict[str, Any], state: TurnState) -> str:
        message_id = arguments.get("message_id")
        if not isinstance(message_id, str):
            return "Identifiant de message manquant."
        message = await self._mailbox.get_message(state.account_id, message_id)
        state.remember([message.header])
        state.show(message_view(message))
        return await self._delegate(
            ANALYST_SYSTEM_PROMPT,
            as_untrusted("message", f"{header_line(message.header)}\ncorps:\n{message.body_text}"),
        )

    async def _request_delete(self, arguments: dict[str, Any], state: TurnState) -> str:
        message_id = arguments.get("message_id")
        if not isinstance(message_id, str):
            return "Identifiant de message manquant."
        target = state.target(message_id)
        if message_id not in state.approved_deletions:
            if all(entry["message_id"] != message_id for entry in state.pending):
                state.pending.append(target)
            return (
                "Suppression non effectuée : elle attend la confirmation explicite de l'opérateur "
                "dans l'interface. Annonce la proposition et arrête-toi là."
            )
        await self._mailbox.move_to_trash(state.account_id, message_id)
        state.deleted.append(target)
        return "Message déplacé vers les éléments supprimés ; il reste récupérable depuis Outlook."

    async def _ui_catalog(self, arguments: dict[str, Any], state: TurnState) -> str:
        if self._ui is None:
            return "Le catalogue d'interface n'est pas disponible : réponds en texte."
        capabilities = arguments.get("capabilities")
        try:
            components = await self._ui.catalog(
                state.account_id,
                query=optional_str(arguments, "query"),
                capabilities=(
                    [entry for entry in capabilities if isinstance(entry, str)]
                    if isinstance(capabilities, list)
                    else None
                ),
                limit=as_integer(arguments.get("limit")),
            )
        except AgentUiError as error:
            return f"Catalogue indisponible (code {error.code}). Réponds en texte."
        for component in components:
            state.catalog[component.id] = component
        if not components:
            return "Aucun composant disponible pour cette session : réponds en texte."
        return _json({"components": [catalog_prompt_payload(c) for c in components]})[:12_000]

    async def _ui_render(self, arguments: dict[str, Any], state: TurnState) -> str:
        if self._ui is None:
            return "L'interface n'est pas disponible : réponds en texte."
        component_id = optional_str(arguments, "component_id")
        fallback_text = optional_str(arguments, "fallback_text")
        if component_id is None or fallback_text is None:
            return "component_id et fallback_text sont obligatoires."
        props = as_record(arguments.get("props"))
        try:
            spec = await self._ui.render(
                state.account_id,
                component_id=component_id,
                component_version=optional_str(arguments, "component_version"),
                props=strip_session_keys(props) if props is not None else {},
                data=data_source(arguments.get("data")),
                fallback_text=fallback_text[:2_000],
            )
        except AgentUiError as error:
            return ui_failure(error)
        if not state.emit(
            {
                "kind": "ui.render",
                "protocolVersion": PROTOCOL_VERSION,
                "id": new_message_id(),
                "role": "assistant",
                "createdAt": now_iso(),
                "ui": spec.to_dict(),
            }
        ):
            return "Trop de composants dans cette réponse : conclus en texte."
        state.ledger.declare(spec.instance_id, spec.component_id, spec.component_version)
        return _json(
            {
                "status": "rendered",
                "instance_id": spec.instance_id,
                "component_id": spec.component_id,
                "component_version": spec.component_version,
                "note": (
                    "Le composant est affiché et charge ses données côté interface. "
                    "Ne répète pas son contenu en texte."
                ),
            }
        )

    async def _ui_update(self, arguments: dict[str, Any], state: TurnState) -> str:
        if self._ui is None:
            return "L'interface n'est pas disponible : réponds en texte."
        instance_id = optional_str(arguments, "instance_id")
        if instance_id is None:
            return "instance_id est obligatoire."
        known = state.ledger.lookup(instance_id)
        if known is None:
            return "Instance inconnue : elle n'est plus affichée. Affiche un composant à jour."
        patch_record = as_record(arguments.get("patch"))
        props = as_record(patch_record.get("props")) if patch_record is not None else None
        if props is None:
            props = patch_record
        if props is None or len(props) == 0:
            return "patch.props doit contenir au moins une prop."
        component_id, component_version = known
        try:
            updated = await self._ui.patch(
                state.account_id,
                instance_id=instance_id,
                component_id=component_id,
                component_version=component_version,
                props=strip_session_keys(props),
            )
        except AgentUiError as error:
            return ui_failure(error)
        if not state.emit(
            {
                "kind": "ui.patch",
                "protocolVersion": PROTOCOL_VERSION,
                "id": new_message_id(),
                "role": "assistant",
                "createdAt": now_iso(),
                "ui": updated.to_dict(),
            }
        ):
            return "Trop de mises à jour dans cette réponse : conclus en texte."
        return _json({"status": "patched", "instance_id": updated.instance_id})

    async def _ui_remove(self, arguments: dict[str, Any], state: TurnState) -> str:
        instance_id = optional_str(arguments, "instance_id")
        if instance_id is None:
            return "instance_id est obligatoire."
        if state.ledger.lookup(instance_id) is None:
            return "Instance inconnue : rien à retirer."
        if not state.emit(
            {
                "kind": "ui.remove",
                "protocolVersion": PROTOCOL_VERSION,
                "id": new_message_id(),
                "role": "assistant",
                "createdAt": now_iso(),
                "ui": {"instanceId": instance_id},
            }
        ):
            return "Trop d'instructions d'interface dans cette réponse."
        state.ledger.known.pop(instance_id, None)
        return _json({"status": "removed", "instance_id": instance_id})

    async def _frame_action(self, event: UiActionEvent, state: TurnState) -> str | None:
        if self._ui is None:
            return None
        try:
            components = await self._ui.catalog(state.account_id)
        except AgentUiError:
            return None
        for entry in components:
            state.catalog[entry.id] = entry
        declared = state.catalog.get(event.component_id)
        if declared is None or event.action_id not in declared.allowed_actions:
            return None
        state.ledger.declare(event.instance_id, event.component_id, event.component_version)
        framed: dict[str, Any] = {
            "instance_id": event.instance_id,
            "component_id": event.component_id,
            "action_id": event.action_id,
            "values": event.values,
        }
        if event.result is not None:
            framed["result"] = event.result
        payload = _json(framed)[:4_000]
        return "\n".join(
            [
                "L'opérateur a interagi avec un composant affiché. Réagis : mets l'instance à jour "
                "avec update_ui_component, affiche le composant attendu, ou réponds en texte. "
                "N'exécute aucune action sensible sans confirmation vérifiée.",
                as_untrusted("action d'interface", payload),
            ]
        )

    async def _delegate(self, system_prompt: str, briefing: str) -> str:
        response = await self._sub_agent.invoke(
            [
                ChatTurn(role="system", content=system_prompt),
                ChatTurn(role="user", content=briefing),
            ]
        )
        return response.content or "Le sous-agent n'a produit aucune synthèse."

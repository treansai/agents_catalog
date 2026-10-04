from typing import Any

import httpx
import pytest
from app.agent_ui.agent_ui import AgentUiError, HttpAgentUiClient, UiActionEvent, UiInstanceRef
from app.assistant.assistant import ALL_TOOL_SCHEMAS, MailboxAssistant
from app.assistant.chat_model import ChatTurn

from tests.fakes import (
    MESSAGE_ID,
    FakeUi,
    InvoiceMailbox,
    ScriptedModel,
    final,
    last_tool_result,
    settings,
    tool_call,
)


def build(turns: list[ChatTurn]) -> tuple[MailboxAssistant, InvoiceMailbox, FakeUi, ScriptedModel]:
    orchestrator = ScriptedModel(turns)
    mailbox = InvoiceMailbox()
    ui = FakeUi()

    def factory(config: Any, *, tools: bool) -> ScriptedModel:
        return orchestrator if tools else ScriptedModel([final("Synthèse.")])

    return MailboxAssistant(settings(), mailbox, factory, ui), mailbox, ui, orchestrator


def test_exposes_the_ui_tools_to_the_model() -> None:
    names = [schema["name"] for schema in ALL_TOOL_SCHEMAS]
    for expected in (
        "get_ui_component_catalog",
        "render_ui_component",
        "update_ui_component",
        "remove_ui_component",
    ):
        assert expected in names


async def test_keeps_a_simple_question_textual() -> None:
    agent, _, ui, _ = build([final("Un agent IA exécute des outils.")])

    answer = await agent.ask("outlook-perso", [{"role": "user", "content": "Qu'est-ce qu'un agent IA ?"}])

    assert answer.ui_messages == []
    assert ui.rendered == []
    assert answer.reply.startswith("Un agent IA")


async def test_renders_a_list_through_an_authorized_resolver() -> None:
    agent, _, ui, _ = build(
        [
            tool_call(
                "get_ui_component_catalog",
                {"query": "tableau de factures", "capabilities": ["display_table"]},
                "t_",
            ),
            tool_call(
                "render_ui_component",
                {
                    "component_id": "mail.list",
                    "component_version": "1.0",
                    "props": {"title": "Mes dernières factures", "pageSize": 20},
                    "data": {
                        "mode": "resolver",
                        "resolver_id": "invoices.search",
                        "input": {"limit": 20, "offset": 0},
                    },
                    "fallback_text": "Voici vos dernières factures.",
                },
                "t_",
            ),
            final("Voici vos vingt dernières factures."),
        ]
    )

    answer = await agent.ask(
        "outlook-perso", [{"role": "user", "content": "Affiche les vingt dernières factures."}]
    )

    assert len(answer.ui_messages) == 1
    message = answer.ui_messages[0]
    assert message["kind"] == "ui.render"
    assert message["ui"]["componentId"] == "mail.list"
    assert message["ui"]["instanceId"].startswith("ui_")
    assert message["ui"]["data"] == {
        "mode": "resolver",
        "resolverId": "invoices.search",
        "input": {"limit": 20, "offset": 0},
    }
    assert ui.catalog_queries[0]["capabilities"] == ["display_table"]


async def test_uses_a_metric_card_for_a_single_indicator() -> None:
    agent, _, _, _ = build(
        [
            tool_call(
                "render_ui_component",
                {
                    "component_id": "metric.card",
                    "props": {"label": "Reçus ce mois"},
                    "data": {"mode": "resolver", "resolver_id": "metrics.receipts", "input": {}},
                    "fallback_text": "Voici le volume de reçus.",
                },
                "t_",
            ),
            final("Volume du mois."),
        ]
    )

    answer = await agent.ask("outlook-perso", [{"role": "user", "content": "Mon chiffre du mois ?"}])

    assert answer.ui_messages[0]["ui"]["componentId"] == "metric.card"


async def test_refuses_an_invented_component_and_tells_the_agent_to_read_the_catalog() -> None:
    agent, _, ui, orchestrator = build(
        [
            tool_call(
                "render_ui_component",
                {
                    "component_id": "billing.invoice-table",
                    "props": {"title": "Factures"},
                    "fallback_text": "Vos factures.",
                },
                "t_",
            ),
            final("Voici vos factures, en texte."),
        ]
    )

    answer = await agent.ask("outlook-perso", [{"role": "user", "content": "Mes factures ?"}])

    assert answer.ui_messages == []
    assert ui.rendered == []
    tool_result = last_tool_result(orchestrator)
    assert "unknown_component" in tool_result
    assert "get_ui_component_catalog" in tool_result


async def test_refuses_invalid_props() -> None:
    agent, _, ui, orchestrator = build(
        [
            tool_call(
                "render_ui_component",
                {"component_id": "mail.list", "props": {"onClick": "alert(1)"}, "fallback_text": "Liste."},
                "t_",
            ),
            final("Je liste vos messages en texte."),
        ]
    )

    answer = await agent.ask("outlook-perso", [{"role": "user", "content": "Liste mes messages."}])

    assert answer.ui_messages == []
    assert ui.rendered == []
    assert "invalid_props" in last_tool_result(orchestrator)


async def test_refuses_an_unauthorized_resolver() -> None:
    agent, _, ui, orchestrator = build(
        [
            tool_call(
                "render_ui_component",
                {
                    "component_id": "metric.card",
                    "props": {"label": "Reçus"},
                    "data": {"mode": "resolver", "resolver_id": "invoices.search", "input": {}},
                    "fallback_text": "Métrique.",
                },
                "t_",
            ),
            final("Impossible d'afficher cette métrique."),
        ]
    )

    answer = await agent.ask("outlook-perso", [{"role": "user", "content": "Combien de reçus ?"}])

    assert answer.ui_messages == []
    assert ui.rendered == []
    assert "unknown_resolver" in last_tool_result(orchestrator)


async def test_never_lets_the_model_supply_the_workspace_or_the_permissions() -> None:
    agent, _, ui, _ = build(
        [
            tool_call(
                "render_ui_component",
                {
                    "component_id": "mail.list",
                    "props": {"title": "Factures"},
                    "data": {
                        "mode": "resolver",
                        "resolver_id": "invoices.search",
                        "input": {
                            "limit": 5,
                            "workspaceId": "autre-workspace",
                            "account_id": "autre-workspace",
                            "userId": "root",
                            "permissions": ["mail.write"],
                        },
                    },
                    "fallback_text": "Factures.",
                },
                "t_",
            ),
            final("Voici."),
        ]
    )

    answer = await agent.ask("outlook-perso", [{"role": "user", "content": "Mes factures ?"}])

    spec = answer.ui_messages[0]
    assert spec["kind"] == "ui.render"
    assert spec["ui"]["data"] == {
        "mode": "resolver",
        "resolverId": "invoices.search",
        "input": {"limit": 5},
    }
    assert ui.rendered[0]["workspace_id"] == "outlook-perso"


async def test_shows_a_confirmation_before_any_deletion_mutation() -> None:
    agent, mailbox, _, _ = build(
        [
            tool_call("list_recent_messages", {"top": 5}, "t_"),
            tool_call("request_delete_message", {"message_id": MESSAGE_ID}, "t_"),
            tool_call(
                "render_ui_component",
                {
                    "component_id": "confirm.dialog",
                    "props": {
                        "title": "Mettre ce message à la corbeille ?",
                        "body": "Le message reste récupérable.",
                        "confirmLabel": "confirmer",
                        "reversible": True,
                        "targetLabel": "Facture 42",
                    },
                    "fallback_text": "Confirmez-vous la mise à la corbeille ?",
                },
                "t_",
            ),
            final("Confirmez-vous ?"),
        ]
    )

    answer = await agent.ask("outlook-perso", [{"role": "user", "content": "Supprime ce message."}])

    assert mailbox.trashed == []
    assert [entry["message_id"] for entry in answer.pending_deletions] == [MESSAGE_ID]
    assert answer.ui_messages[0]["ui"]["componentId"] == "confirm.dialog"


async def test_updates_a_component_after_a_mutation() -> None:
    agent, _, ui, _ = build(
        [
            tool_call(
                "update_ui_component",
                {"instance_id": "ui_live_1", "patch": {"props": {"title": "Corbeille faite"}}},
                "t_",
            ),
            final("C'est fait."),
        ]
    )

    answer = await agent.ask(
        "outlook-perso",
        [{"role": "user", "content": "Marque-le comme traité."}],
        [],
        None,
        [UiInstanceRef(instance_id="ui_live_1", component_id="mail.list", component_version="1.0")],
    )

    assert answer.ui_messages[0]["kind"] == "ui.patch"
    assert answer.ui_messages[0]["ui"]["patch"] == {"title": "Corbeille faite"}
    assert ui.patched[0]["instance_id"] == "ui_live_1"


async def test_refuses_a_patch_on_an_unknown_instance() -> None:
    agent, _, ui, orchestrator = build(
        [
            tool_call(
                "update_ui_component",
                {"instance_id": "ui_inventee", "patch": {"props": {"title": "x"}}},
                "t_",
            ),
            final("Je ne peux pas mettre à jour ce composant."),
        ]
    )

    answer = await agent.ask("outlook-perso", [{"role": "user", "content": "Mets à jour."}])

    assert answer.ui_messages == []
    assert ui.patched == []
    assert "Instance inconnue" in last_tool_result(orchestrator)


async def test_replays_a_user_click_as_untrusted_input() -> None:
    agent, mailbox, _, orchestrator = build(
        [tool_call("read_message", {"message_id": MESSAGE_ID}, "t_"), final("Voici le message demandé.")]
    )
    action = UiActionEvent(
        event_id="evt-1",
        message_id="msg-1",
        instance_id="ui_live_1",
        component_id="mail.list",
        component_version="1.0",
        action_id="messages.open",
        values={"targetId": MESSAGE_ID},
        idempotency_key="idem-1",
    )

    answer = await agent.ask(
        "outlook-perso",
        [{"role": "user", "content": "Affiche mes factures."}],
        [],
        action,
        [UiInstanceRef(instance_id="ui_live_1", component_id="mail.list", component_version="1.0")],
    )

    assert "get_message" in mailbox.calls
    framed = str(orchestrator.prompts[0][-1].content)
    assert "BEGIN UNTRUSTED" in framed
    assert "messages.open" in framed
    assert answer.reply == "Voici le message demandé."


async def test_never_replays_an_action_absent_from_the_catalog() -> None:
    agent, mailbox, _, orchestrator = build([final("jamais appelé")])

    answer = await agent.ask(
        "outlook-perso",
        [{"role": "user", "content": "Affiche mes factures."}],
        [],
        UiActionEvent(
            event_id="evt-2",
            message_id="msg-2",
            instance_id="ui_live_1",
            component_id="mail.list",
            component_version="1.0",
            action_id="account.transfer",
            values={},
            idempotency_key="idem-2",
        ),
    )

    assert answer.ui_messages == []
    assert mailbox.calls == []
    assert orchestrator.prompts == []
    assert "pas autorisée" in answer.reply


async def test_does_not_create_a_component_from_a_prompt_injection_inside_a_message() -> None:
    agent, _, ui, _ = build(
        [
            tool_call("read_message", {"message_id": MESSAGE_ID}, "t_"),
            final("Ce message tente une injection ; je l'ignore."),
        ]
    )

    answer = await agent.ask("outlook-perso", [{"role": "user", "content": "Ouvre ce message."}])

    assert ui.rendered == []
    assert all(
        message["kind"] != "ui.render" or message["ui"]["componentId"] != "billing.invoice-table"
        for message in answer.ui_messages
    )


async def test_bounds_the_number_of_ui_messages_in_a_turn() -> None:
    render = tool_call(
        "render_ui_component",
        {"component_id": "metric.card", "props": {"label": "Reçus"}, "fallback_text": "Métrique."},
        "t_",
    )
    agent, _, _, _ = build([*([render] * 8), final("Fin.")])

    answer = await agent.ask("outlook-perso", [{"role": "user", "content": "Affiche tout."}])

    assert len(answer.ui_messages) <= 6


async def test_maps_backend_refusals_to_catalogue_codes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/render"):
            return httpx.Response(400, json={"message": "invalid_props", "statusCode": 400})
        return httpx.Response(
            200,
            json={
                "protocolVersion": "1.0",
                "components": [
                    {
                        "id": "mail.list",
                        "version": "1.0",
                        "title": "Liste",
                        "description": "Tableau",
                        "capabilities": ["display_table"],
                        "useWhen": ["factures"],
                        "propsSchema": {"type": "object"},
                        "allowedDataResolvers": ["invoices.search"],
                        "allowedActions": ["messages.open"],
                    }
                ],
            },
        )

    client = HttpAgentUiClient("http://backend:8080", "backend-secret", httpx.MockTransport(handler))
    components = await client.catalog("outlook-perso", query="factures")
    assert [component.id for component in components] == ["mail.list"]

    with pytest.raises(AgentUiError) as raised:
        await client.render(
            "outlook-perso",
            component_id="mail.list",
            component_version="1.0",
            props={"onClick": "alert(1)"},
            fallback_text="Liste.",
        )
    assert raised.value.code == "invalid_props"


async def test_never_produces_two_consecutive_assistant_turns_from_an_interaction() -> None:
    agent, _, _, orchestrator = build([final("Voici.")])

    await agent.ask(
        "outlook-perso",
        [
            {"role": "user", "content": "Affiche mes factures."},
            {"role": "assistant", "content": "Voici vos factures."},
            {"role": "assistant", "content": "Le message est ouvert."},
        ],
    )

    roles = [message.role for message in orchestrator.prompts[0]]
    for index in range(1, len(roles)):
        assert roles[index] != roles[index - 1]


async def test_opens_the_map_component_on_a_real_resolver_for_a_trip_request() -> None:
    agent, _, ui, _ = build(
        [
            tool_call(
                "get_ui_component_catalog",
                {"query": "trajet vers un restaurant", "capabilities": ["display_map"]},
                "t_",
            ),
            tool_call(
                "render_ui_component",
                {
                    "component_id": "map.route",
                    "component_version": "1.0",
                    "props": {"title": "Trajet vers Le Rival"},
                    "data": {
                        "mode": "resolver",
                        "resolver_id": "places.route",
                        "input": {"to": "Le Rival, Paris", "mode": "walking"},
                    },
                    "fallback_text": "Je n'ai pas pu afficher le trajet vers Le Rival.",
                },
                "t_",
            ),
            final("Voici le trajet à pied vers Le Rival."),
        ]
    )

    answer = await agent.ask(
        "outlook-perso",
        [{"role": "user", "content": "Trouve le restaurant Le Rival et propose-moi un trajet à pied."}],
    )

    message = answer.ui_messages[0]
    assert message["kind"] == "ui.render"
    assert message["ui"]["componentId"] == "map.route"
    assert message["ui"]["data"] == {
        "mode": "resolver",
        "resolverId": "places.route",
        "input": {"to": "Le Rival, Paris", "mode": "walking"},
    }
    assert ui.catalog_queries[0]["capabilities"] == ["display_map"]


async def test_cannot_reach_a_map_provider_of_its_own_choosing() -> None:
    agent, _, ui, orchestrator = build(
        [
            tool_call(
                "render_ui_component",
                {
                    "component_id": "map.route",
                    "props": {"title": "Trajet"},
                    "data": {
                        "mode": "resolver",
                        "resolver_id": "http.fetch",
                        "input": {"url": "http://169.254.169.254/latest/meta-data"},
                    },
                    "fallback_text": "Trajet.",
                },
                "t_",
            ),
            final("Je ne peux pas afficher ce trajet."),
        ]
    )

    answer = await agent.ask("outlook-perso", [{"role": "user", "content": "Trajet vers Le Rival ?"}])

    assert answer.ui_messages == []
    assert ui.rendered == []
    assert "unknown_resolver" in last_tool_result(orchestrator)

"""Exécution des composants d'interface demandés par l'agent : catalogue, rendu, résolveurs, actions."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import httpx
import pytest

from tests.conftest import API_KEY

AUTH = {"X-API-Key": API_KEY}


def hexdigest(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def receipt(account_id: str, index: int) -> dict[str, Any]:
    return {
        "analysis_id": hexdigest(f"receipt:{account_id}:{index}"),
        "message_ref": f"outlook:{account_id}:invoice-{index}",
        "content_hash": hexdigest(f"hash:{account_id}:{index}"),
        "pipeline_version": "ezer-ts-v1",
        "model_id": "ezer-typescript-rules-v1",
        "prompt_version": "heuristic-fr-v1",
        "created_at": f"2026-08-27T08:{index % 60:02d}:00.000Z",
        "category": "receipt",
        "priority": "normal",
        "needs_human_review": False,
        "summary": f"Facture {index} — paiement reçu",
        "key_points": [f"Montant de la facture {index}"],
        "action_items": [],
        "safety": {
            "risk_level": "none",
            "prompt_injection_detected": False,
            "phishing_likelihood": 0.01,
            "indicators": [],
            "rationale": "Reçu de paiement.",
            "confidence": 0.9,
        },
        "triage": {
            "category": "receipt",
            "priority": "normal",
            "needs_human_review": False,
            "confidence": 0.9,
            "rationale": "Facture",
        },
        "detected_language": "fr",
    }


async def offline_fetch(url, **_kwargs) -> httpx.Response:
    """Aucun fournisseur de carte joignable : le résolveur doit retomber sur ses lieux connus."""
    raise httpx.ConnectError("offline")


@pytest.fixture
def client(start_app, work_dir):
    accounts = [{"id": "workspace-a", "provider": "outlook"}, {"id": "workspace-b", "provider": "gmail"}]
    data_file = work_dir / "ezer.json"
    data_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "accounts": accounts,
                "analyses": [*(receipt("workspace-a", index) for index in range(24)), receipt("workspace-b", 99)],
                "cursors": {"workspace-a": None, "workspace-b": None},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    data_file.chmod(0o600)
    return start_app(
        {
            "EZER_MODE": "configured",
            "EZER_API_KEY": API_KEY,
            "EZER_DATA_FILE": str(data_file),
            "EZER_ACCOUNTS_JSON": json.dumps(accounts),
            "EZER_TOKEN_FILE": str(work_dir / "tokens.json"),
        },
        http_fetch=offline_fetch,
        grant_write_permission=True,
    )


def post(client, path: str, body: Any):
    return client.post(path, headers={**AUTH, "Content-Type": "application/json"}, json=body)


def resolve(client, workspace: str, **body: Any):
    return post(client, f"/v1/agent-ui/resolve?workspace_id={workspace}", body)


def action_event(**overrides: Any) -> dict[str, Any]:
    event = {
        "kind": "ui.action",
        "eventId": "evt-1",
        "messageId": "msg-1",
        "instanceId": "inst-1",
        "componentId": "mail.list",
        "componentVersion": "1.0",
        "actionId": "messages.open",
        "values": {},
        "idempotencyKey": "idem-1",
    }
    event.update(overrides)
    return event


def test_returns_a_catalog_without_import_paths(client):
    response = client.get("/v1/agent-ui/catalog?workspace_id=workspace-a", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["protocolVersion"] == "1.0"
    assert "mail.list" in [entry["id"] for entry in response.json()["components"]]
    assert re.search(r"import\(|graph\.microsoft|EZER_API_KEY", json.dumps(response.json())) is None
    assert all("requiredPermissions" not in entry for entry in response.json()["components"])


def test_resolves_a_paginated_invoice_list_in_the_current_workspace(client):
    response = resolve(
        client,
        "workspace-a",
        resolverId="invoices.search",
        componentId="mail.list",
        instanceId="inst-invoices",
        input={"limit": 10, "offset": 0, "query": "facture"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert len(body["data"]["items"]) == 10
    assert body["data"]["total"] == 24
    assert re.search(r"Facture", body["data"]["items"][0]["subject"])


def test_isolates_data_between_two_workspaces(client):
    alpha = resolve(
        client,
        "workspace-a",
        resolverId="invoices.search",
        componentId="mail.list",
        instanceId="a",
        input={"limit": 25, "offset": 0},
    )
    beta = resolve(
        client,
        "workspace-b",
        resolverId="invoices.search",
        componentId="mail.list",
        instanceId="b",
        input={"limit": 25, "offset": 0},
    )
    assert alpha.status_code == beta.status_code == 200
    assert alpha.json()["data"]["total"] == 24
    assert beta.json()["data"]["total"] == 1
    assert beta.json()["data"]["items"][0]["id"] == "invoice-99"


def test_refuses_a_resolver_not_allowed_for_the_component(client):
    response = resolve(
        client,
        "workspace-a",
        resolverId="messages.get",
        componentId="mail.list",
        instanceId="x",
        input={"message_id": "nope"},
    )
    assert response.status_code == 400


def test_refuses_an_unknown_component_id(client):
    response = resolve(
        client,
        "workspace-a",
        resolverId="invoices.search",
        componentId="evil.widget",
        instanceId="x",
        input={},
    )
    assert response.status_code == 400


def test_refuses_an_unknown_workspace(client):
    assert client.get("/v1/agent-ui/catalog?workspace_id=workspace-z", headers=AUTH).status_code == 403
    assert client.get("/v1/agent-ui/catalog", headers=AUTH).status_code == 400
    assert client.get("/v1/agent-ui/catalog?workspace_id=workspace-a&bogus=1", headers=AUTH).status_code == 400
    assert client.get("/v1/agent-ui/catalog?workspace_id=workspace-a").status_code == 401


def test_paginates_a_large_table_on_the_server_side(client):
    page = resolve(
        client,
        "workspace-a",
        resolverId="invoices.search",
        componentId="mail.list",
        instanceId="page-2",
        input={"limit": 10, "offset": 10, "query": "facture"},
    )
    assert page.status_code == 200
    assert len(page.json()["data"]["items"]) == 10
    assert page.json()["data"]["offset"] == 10
    assert page.json()["data"]["items"][0]["id"] != "invoice-0"


def test_exposes_a_receipts_metric_card(client, monkeypatch):
    # Les factures de test datent d'août 2026 : on fige l'horloge dans ce mois.
    monkeypatch.setattr("app.agent_ui.resolvers.now_iso", lambda: "2026-08-28T10:00:00.000Z")
    response = resolve(
        client,
        "workspace-a",
        resolverId="metrics.receipts",
        componentId="metric.card",
        instanceId="metric-1",
        input={},
    )
    assert response.status_code == 200
    assert response.json()["data"]["label"] == "reçus ce mois"
    assert response.json()["data"]["value"] == "24"
    assert response.json()["data"]["series"] == [1] * 24


def test_refuses_an_unknown_action(client):
    response = post(client, "/v1/agent-ui/action?workspace_id=workspace-a", action_event(actionId="rm -rf"))
    assert response.status_code == 400


def test_requires_a_confirmation_before_mutation_and_protects_against_double_click(client):
    payload = action_event(
        eventId="evt-del-1",
        actionId="messages.trash",
        values={"targetId": "invoice-1"},
        idempotencyKey="idem-del-1",
    )
    first = post(client, "/v1/agent-ui/action?workspace_id=workspace-a", payload)
    second = post(client, "/v1/agent-ui/action?workspace_id=workspace-a", payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "confirmation_required"
    assert first.json()["result"] is None
    assert first.json()["confirmation"]["reversible"] is True
    assert second.json()["confirmation"]["confirmationId"] == first.json()["confirmation"]["confirmationId"]
    assert set(first.json()["confirmation"]) == {
        "confirmationId",
        "action",
        "target",
        "impact",
        "reversible",
        "token",
    }

    bad = post(
        client,
        "/v1/agent-ui/action?workspace_id=workspace-a",
        action_event(
            eventId="evt-del-bad",
            componentId="confirm.dialog",
            actionId="confirmation.confirm",
            values={
                "confirmationId": first.json()["confirmation"]["confirmationId"],
                "confirmationToken": "not-the-token",
            },
            idempotencyKey="idem-del-bad",
        ),
    )
    assert bad.status_code == 409


def test_a_confirmation_cannot_be_used_from_another_workspace(client):
    first = post(
        client,
        "/v1/agent-ui/action?workspace_id=workspace-a",
        action_event(actionId="messages.trash", values={"targetId": "invoice-1"}, idempotencyKey="idem-x"),
    ).json()["confirmation"]

    stolen = post(
        client,
        "/v1/agent-ui/action?workspace_id=workspace-b",
        action_event(
            componentId="confirm.dialog",
            actionId="confirmation.confirm",
            values={"confirmationId": first["confirmationId"], "confirmationToken": first["token"]},
            idempotencyKey="idem-stolen",
        ),
    )
    assert stolen.status_code == 409


def test_does_not_execute_javascript_supplied_in_a_payload(client):
    response = resolve(
        client,
        "workspace-a",
        resolverId="invoices.search",
        componentId="mail.list",
        instanceId="xss",
        input={"query": "<script>alert(1)</script>", "extra": "nope", "constructor": {"prototype": {"polluted": True}}},
    )
    assert response.status_code == 400


def test_accepts_a_declarative_click_on_a_row(client):
    response = post(
        client,
        "/v1/agent-ui/action?workspace_id=workspace-a",
        action_event(
            eventId="evt-open-1",
            values={"targetId": "invoice-1"},
            idempotencyKey="idem-open-1",
        ),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["result"]["targetId"] == "invoice-1"
    assert response.json()["result"]["kind"] == "open"


def test_filters_the_catalog_by_capability_and_keywords(client):
    response = client.get(
        "/v1/agent-ui/catalog?workspace_id=workspace-a&query=tableau%20de%20factures"
        "&capabilities=display_table&limit=5",
        headers=AUTH,
    )
    assert response.status_code == 200
    ids = [entry["id"] for entry in response.json()["components"]]
    assert "mail.list" in ids
    assert "confirm.dialog" not in ids
    assert len(ids) <= 5


def test_validates_a_display_proposal_and_mints_the_instance_id_on_the_server(client):
    response = post(
        client,
        "/v1/agent-ui/render?workspace_id=workspace-a",
        {
            "componentId": "mail.list",
            "componentVersion": "1.0",
            "props": {"title": "Mes dernières factures", "pageSize": 20},
            "data": {
                "mode": "resolver",
                "resolverId": "invoices.search",
                "input": {"limit": 20, "offset": 0, "query": "facture"},
            },
            "fallbackText": "Voici vos dernières factures.",
        },
    )
    assert response.status_code == 200
    ui = response.json()["ui"]
    assert response.json()["status"] == "success"
    assert re.fullmatch(r"ui_[0-9a-f]{24}", ui["instanceId"])
    assert ui["componentId"] == "mail.list"
    assert ui["componentVersion"] == "1.0"
    assert ui["data"]["resolverId"] == "invoices.search"
    assert ui["data"]["mode"] == "resolver"
    assert ui["fallbackText"] == "Voici vos dernières factures."


def test_refuses_to_render_an_invented_component(client):
    response = post(
        client,
        "/v1/agent-ui/render?workspace_id=workspace-a",
        {"componentId": "billing.invoice-table", "props": {}, "fallbackText": "Voici vos factures."},
    )
    assert response.status_code == 400


def test_refuses_props_outside_the_schema(client):
    response = post(
        client,
        "/v1/agent-ui/render?workspace_id=workspace-a",
        {"componentId": "mail.list", "props": {"title": "ok", "onClick": "alert(1)"}, "fallbackText": "Liste."},
    )
    assert response.status_code == 400


def test_refuses_a_resolver_not_allowed_at_render_time(client):
    response = post(
        client,
        "/v1/agent-ui/render?workspace_id=workspace-a",
        {
            "componentId": "metric.card",
            "props": {"label": "Reçus"},
            "data": {"mode": "resolver", "resolverId": "messages.get", "input": {"message_id": "x"}},
            "fallbackText": "Métrique.",
        },
    )
    assert response.status_code == 400


def test_refuses_an_outdated_component_version(client):
    response = post(
        client,
        "/v1/agent-ui/render?workspace_id=workspace-a",
        {"componentId": "mail.list", "componentVersion": "0.9", "props": {}, "fallbackText": "Liste."},
    )
    assert response.status_code == 409


def test_strips_the_identity_supplied_by_the_model_from_a_resolver_input(client):
    response = post(
        client,
        "/v1/agent-ui/render?workspace_id=workspace-a",
        {
            "componentId": "mail.list",
            "props": {},
            "data": {
                "mode": "resolver",
                "resolverId": "invoices.search",
                "input": {"limit": 5, "workspaceId": "workspace-b", "account_id": "workspace-b", "userId": "root"},
            },
            "fallbackText": "Liste.",
        },
    )
    assert response.status_code == 200
    assert response.json()["ui"]["data"]["input"] == {"limit": 5}


def test_validates_a_partial_props_patch(client):
    response = post(
        client,
        "/v1/agent-ui/patch?workspace_id=workspace-a",
        {
            "instanceId": "ui_0123456789abcdef01234567",
            "componentId": "mail.list",
            "componentVersion": "1.0",
            "props": {"title": "Factures de juillet"},
        },
    )
    assert response.status_code == 200
    assert response.json()["ui"] == {
        "instanceId": "ui_0123456789abcdef01234567",
        "patch": {"title": "Factures de juillet"},
    }

    rejected = post(
        client,
        "/v1/agent-ui/patch?workspace_id=workspace-a",
        {"instanceId": "ui_0123456789abcdef01234567", "componentId": "mail.list", "props": {"unknownProp": 1}},
    )
    assert rejected.status_code == 400

    empty = post(
        client,
        "/v1/agent-ui/patch?workspace_id=workspace-a",
        {"instanceId": "ui_0123456789abcdef01234567", "componentId": "mail.list", "props": {}},
    )
    assert empty.status_code == 400


def test_exposes_the_map_component_and_its_route_resolver(client):
    response = client.get(
        "/v1/agent-ui/catalog?workspace_id=workspace-a&query=trajet%20restaurant&capabilities=display_map",
        headers=AUTH,
    )
    assert response.status_code == 200
    map_component = next(entry for entry in response.json()["components"] if entry["id"] == "map.route")
    assert map_component["allowedDataResolvers"] == ["places.route"]
    assert re.search(r"maptiler|api\.|key=", json.dumps(map_component), re.I) is None


def test_draws_a_route_to_a_place_named_by_the_agent(client):
    response = resolve(
        client,
        "workspace-a",
        resolverId="places.route",
        componentId="map.route",
        instanceId="inst-map",
        input={"to": "Le Rival", "from": "Gare de Lyon", "mode": "walking"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert re.search(r"Rival", data["destination"]["name"])
    assert re.search(r"Gare de Lyon", data["origin"]["name"])
    assert data["mode"] == "walking"
    assert data["distanceKm"] > 0
    assert data["durationMin"] > 0
    assert len(data["coordinates"]) >= 2
    # Sans service de routage configuré, la sortie s'annonce comme une estimation.
    assert data["estimated"] is True


def test_displays_a_route_map_validated_by_the_catalog(client):
    response = post(
        client,
        "/v1/agent-ui/render?workspace_id=workspace-a",
        {
            "componentId": "map.route",
            "componentVersion": "1.0",
            "props": {"title": "Trajet vers Le Rival"},
            "data": {
                "mode": "resolver",
                "resolverId": "places.route",
                "input": {"to": "Le Rival, Paris", "mode": "walking"},
            },
            "fallbackText": "Je n'ai pas pu afficher le trajet.",
        },
    )
    assert response.status_code == 200
    assert response.json()["ui"]["componentId"] == "map.route"
    assert response.json()["ui"]["data"]["input"] == {"to": "Le Rival, Paris", "mode": "walking"}


def test_refuses_a_route_input_outside_the_schema_url_included(client):
    assert (
        resolve(
            client,
            "workspace-a",
            resolverId="places.route",
            componentId="map.route",
            instanceId="inst-map",
            input={"to": "Le Rival", "endpoint": "http://169.254.169.254/latest/meta-data"},
        ).status_code
        == 400
    )
    assert (
        resolve(
            client,
            "workspace-a",
            resolverId="places.route",
            componentId="map.route",
            instanceId="inst-map",
            input={"to": "Le Rival", "mode": "teleportation"},
        ).status_code
        == 400
    )


def test_refuses_an_unknown_place_without_inventing_coordinates(client):
    response = resolve(
        client,
        "workspace-a",
        resolverId="places.route",
        componentId="map.route",
        instanceId="inst-map",
        input={"to": "Restaurant totalement inexistant zzz"},
    )
    assert response.status_code == 422


def test_does_not_allow_the_route_resolver_on_another_component(client):
    response = resolve(
        client,
        "workspace-a",
        resolverId="places.route",
        componentId="mail.list",
        instanceId="inst-map",
        input={"to": "Le Rival"},
    )
    assert response.status_code == 400


def test_an_empty_result_is_reported_as_empty(client):
    response = resolve(
        client,
        "workspace-a",
        resolverId="senders.tally",
        componentId="senders.list",
        instanceId="inst-senders",
        input={},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "empty", "data": {"items": []}}


def test_validation_precedes_the_workspace_check_and_errors_are_neutral(client):
    # 400 (corps invalide) avant 403 (espace inconnu), et jamais le code interne dans la réponse.
    response = post(client, "/v1/agent-ui/render?workspace_id=workspace-z", {"componentId": "mail.list"})
    assert response.status_code == 400
    assert response.json()["message"] == "invalid request"

    failure = post(
        client,
        "/v1/agent-ui/render?workspace_id=workspace-a",
        {"componentId": "mail.list", "componentVersion": "0.9", "props": {}, "fallbackText": "Liste."},
    )
    assert failure.status_code == 409
    assert failure.json()["message"] == "request failed"
    assert set(failure.json()) == {"statusCode", "message", "detail", "request_id"}

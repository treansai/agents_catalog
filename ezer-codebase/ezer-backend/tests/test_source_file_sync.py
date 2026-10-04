"""Mode configured : le connecteur catalogue lit EZER_SOURCE_FILE à chaque page."""

from __future__ import annotations

import json

from tests.conftest import API_KEY

AUTH = {"X-API-Key": API_KEY}


def source_message(identifier: str, **overrides):
    message = {
        "account_id": "operations",
        "provider": "outlook",
        "provider_message_id": identifier,
        "subject": "Validation requise",
        "sender_name": "Équipe opérations",
        "sender_address": "ops@example.test",
        "received_at": "2026-08-27T08:00:00Z",
        "body_text": "Merci de valider le planning avant vendredi.",
        "snippet": "Validation du planning avant vendredi",
    }
    message.update(overrides)
    return message


def environment(work_dir, source):
    return {
        "EZER_MODE": "configured",
        "EZER_API_KEY": API_KEY,
        "EZER_DATA_FILE": str(work_dir / "ezer.json"),
        "EZER_SOURCE_FILE": str(source),
        "EZER_ACCOUNTS_JSON": json.dumps([{"id": "operations", "provider": "outlook"}]),
    }


def test_reads_messages_from_the_source_file_and_rereads_it_on_every_page(start_app, work_dir):
    source = work_dir / "messages.json"
    source.write_text(json.dumps({"messages": [source_message("message-001")]}), encoding="utf-8")
    client = start_app(environment(work_dir, source))

    first = client.post("/v1/sync", headers=AUTH, json={}).json()["reports"][0]
    assert (first["fetched"], first["processed"], first["cursor_advanced"]) == (1, 1, True)
    assert first["analyses"][0]["message_ref"] == "outlook:operations:message-001"
    assert first["analyses"][0]["created_at"] != "2026-08-27T08:00:00.000Z"  # horodatage de l'analyse
    assert first["analyses"][0]["pipeline_version"] == "ezer-ts-v1"

    # Le fichier est mis à jour sans redémarrer le serveur ; un tableau nu est aussi accepté.
    source.write_text(json.dumps([source_message("message-001"), source_message("message-002")]), encoding="utf-8")
    second = client.post("/v1/sync", headers=AUTH, json={}).json()["reports"][0]
    assert (second["fetched"], second["processed"]) == (1, 1)
    assert second["analyses"][0]["message_ref"] == "outlook:operations:message-002"

    third = client.post("/v1/sync", headers=AUTH, json={}).json()["reports"][0]
    assert third["fetched"] == 0


def test_without_a_source_file_a_configured_connector_returns_an_empty_page(start_app, work_dir):
    environment_ = environment(work_dir, work_dir / "unused.json")
    environment_["EZER_SOURCE_FILE"] = None
    client = start_app(environment_)

    report = client.post("/v1/sync", headers=AUTH, json={"limit": 5}).json()["reports"][0]
    assert report["fetched"] == 0 and report["failed"] == 0


def test_an_invalid_source_file_is_an_internal_error_without_details(start_app, work_dir):
    source = work_dir / "messages.json"
    source.write_text(json.dumps([source_message("m", unexpected=True)]), encoding="utf-8")
    client = start_app(environment(work_dir, source))

    response = client.post("/v1/sync", headers=AUTH, json={})
    assert response.status_code == 500
    assert response.json()["message"] == "internal server error"
    assert "unknown fields" not in response.text

    source.write_text("{broken", encoding="utf-8")
    assert client.post("/v1/sync", headers=AUTH, json={}).status_code == 500

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent_ui.places import js_round
from app.config import Settings
from app.demo.data import DEMO_MESSAGES
from app.domain.models import Account
from app.mail.graph_connector import trusted_graph_url
from app.mail.outlook_auth import grants_write_access, refresh_scopes_for
from app.mail.token_store import TokenStore
from app.persistence.json_store import JsonPersistence, parse_state
from app.sync.catalog_connector import CatalogConnector
from app.web.auth import api_key_matches
from tests.conftest import API_KEY


def settings(monkeypatch: pytest.MonkeyPatch, **environment: str) -> Settings:
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_defaults_match_the_previous_service(monkeypatch):
    config = settings(monkeypatch)
    assert config.mode == "demo"
    assert config.api_key == "ezer-demo-key"
    assert config.host == "127.0.0.1"
    assert config.port == 8080
    assert config.data_file == Path("./data/ezer.json").resolve()
    assert config.token_file == Path("./data/tokens.json").resolve()
    assert config.sync_default_limit == 50
    assert config.sync_max_limit == 500
    assert config.nominatim_url == "https://nominatim.openstreetmap.org"
    assert config.maptiler_style == "streets-v2-dark"
    assert config.default_origin == "Paris, France"
    assert [account.id for account in config.accounts] == ["gmail-primary", "outlook-ops"]


def test_only_ezer_variables_are_read(monkeypatch):
    monkeypatch.setenv("PORT", "1234")
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("MODE", "configured")
    config = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (config.port, config.host, config.mode) == (8080, "127.0.0.1", "demo")


def test_the_env_file_is_read_but_the_environment_wins(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("EZER_PORT=9001\nEZER_HOST=0.0.0.0\n", encoding="utf-8")
    monkeypatch.setenv("EZER_PORT", "9002")
    config = Settings()  # type: ignore[call-arg]
    assert (config.port, config.host) == (9002, "0.0.0.0")


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"EZER_MODE": "other"}, "EZER_MODE must be demo or configured"),
        ({"EZER_MODE": "configured"}, "EZER_API_KEY is required in configured mode"),
        ({"EZER_API_KEY": "short"}, "between 8 and 512 characters"),
        ({"EZER_PORT": "abc"}, "EZER_PORT must be an integer"),
        ({"EZER_PORT": "70000"}, "EZER_PORT must be between 1 and 65535"),
        ({"EZER_DEMO_RESET_ON_START": "yes"}, "EZER_DEMO_RESET_ON_START must be true or false"),
        ({"EZER_SYNC_DEFAULT_LIMIT": "0"}, "between 1 and 500"),
        ({"EZER_SYNC_DEFAULT_LIMIT": "100", "EZER_SYNC_MAX_LIMIT": "50"}, "cannot exceed"),
        ({"EZER_OUTLOOK_CLIENT_ID": "not a client id"}, "application identifier"),
        ({"EZER_MAPTILER_KEY": "bad key!"}, "must be an API key"),
        ({"EZER_ROUTING_URL": "ftp://routing.example"}, "must be an http(s) URL"),
        ({"EZER_ROUTING_URL": "not a url"}, "must be an absolute URL"),
        ({"EZER_NOMINATIM_URL": "nope"}, "must be an absolute URL"),
    ],
)
def test_rejects_invalid_configuration(monkeypatch, environment, message):
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match=re.escape(message)):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("accounts", "message"),
    [
        ("not json", "must contain valid JSON"),
        ('{"id": "a"}', "must be an array"),
        ('["a"]', "entry 0 must be an object"),
        ('[{"id": "a", "provider": "gmail", "x": 1}]', "unknown fields"),
        ('[{"id": "bad id", "provider": "gmail"}]', "invalid id"),
        ('[{"id": "a", "provider": "yahoo"}]', "invalid provider"),
        ('[{"id": "a", "provider": "outlook", "mailbox": "nope"}]', "invalid mailbox"),
        ('[{"id": "a", "provider": "gmail"}, {"id": "a", "provider": "gmail"}]', "duplicate account ids"),
    ],
)
def test_rejects_invalid_accounts(monkeypatch, accounts, message):
    monkeypatch.setenv("EZER_MODE", "configured")
    monkeypatch.setenv("EZER_API_KEY", API_KEY)
    monkeypatch.setenv("EZER_ACCOUNTS_JSON", accounts)
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_configured_accounts_lowercase_the_mailbox_and_demo_ignores_the_variable(monkeypatch):
    monkeypatch.setenv("EZER_ACCOUNTS_JSON", "not json")
    assert len(Settings(_env_file=None).accounts) == 2  # type: ignore[call-arg]
    monkeypatch.setenv("EZER_MODE", "configured")
    monkeypatch.setenv("EZER_API_KEY", API_KEY)
    monkeypatch.setenv("EZER_ACCOUNTS_JSON", '[{"id":"a","provider":"outlook","mailbox":"Marc@Outlook.COM"}]')
    assert Settings(_env_file=None).accounts == [  # type: ignore[call-arg]
        Account(id="a", provider="outlook", mailbox="marc@outlook.com")
    ]


def test_urls_lose_their_trailing_slash_and_an_empty_nominatim_url_disables_the_fallback(monkeypatch):
    config = settings(monkeypatch, EZER_ROUTING_URL="https://routing.interne.example/", EZER_NOMINATIM_URL="  ")
    assert config.routing_url == "https://routing.interne.example"
    assert config.nominatim_url is None


def test_api_key_comparison_is_exact_and_tolerates_any_length():
    assert api_key_matches("secret-key", "secret-key")
    assert not api_key_matches("secret-kez", "secret-key")
    assert not api_key_matches("", "secret-key")
    assert not api_key_matches(None, "secret-key")
    assert not api_key_matches("secret-key" * 1000, "secret-key")


# --- persistance -------------------------------------------------------------------------------------


def run(coroutine):
    return asyncio.run(coroutine)


def test_reload_reads_the_existing_file_and_reconciles_accounts(monkeypatch, tmp_path):
    data_file = tmp_path / "data" / "ezer.json"
    first = settings(monkeypatch, EZER_DATA_FILE=str(data_file))
    store = JsonPersistence(first)
    run(store.initialize())
    assert (data_file.parent.stat().st_mode & 0o777) == 0o700
    assert run(store.set_cursor("gmail-primary", "3", "5"))
    assert not run(store.set_cursor("gmail-primary", "3", "6")), "le curseur attendu ne correspond plus"
    assert not run(store.set_cursor("gmail-primary", "5", "5")), "aucun changement : rien à écrire"

    monkeypatch.setenv("EZER_MODE", "configured")
    monkeypatch.setenv("EZER_API_KEY", API_KEY)
    monkeypatch.setenv(
        "EZER_ACCOUNTS_JSON",
        json.dumps([{"id": "gmail-primary", "provider": "gmail"}, {"id": "new", "provider": "gmail"}]),
    )
    second = JsonPersistence(Settings(_env_file=None))  # type: ignore[call-arg]
    run(second.initialize())

    assert [account["id"] for account in run(second.list_accounts())] == ["gmail-primary", "new"]
    assert run(second.get_cursor("gmail-primary")) == "5"
    assert run(second.get_cursor("new")) is None
    # Les analyses d'un compte retiré de la configuration disparaissent des listes.
    assert run(second.list_analyses(100, 0))["total"] == 3
    removed = json.loads(data_file.read_text(encoding="utf-8"))["analyses"]
    outlook = next(item["analysis_id"] for item in removed if ":outlook-ops:" in item["message_ref"])
    assert run(second.get_analysis(outlook)) is None


def test_demo_reset_on_start_rewrites_the_file(monkeypatch, tmp_path):
    data_file = tmp_path / "ezer.json"
    config = settings(monkeypatch, EZER_DATA_FILE=str(data_file), EZER_DEMO_RESET_ON_START="true")
    store = JsonPersistence(config)
    run(store.initialize())
    run(store.set_cursor("gmail-primary", "3", "4"))
    run(JsonPersistence(config).initialize())
    assert json.loads(data_file.read_text(encoding="utf-8"))["cursors"]["gmail-primary"] == "3"


def test_saving_the_same_analysis_twice_is_idempotent(monkeypatch, tmp_path):
    config = settings(monkeypatch, EZER_DATA_FILE=str(tmp_path / "ezer.json"))
    store = JsonPersistence(config)
    run(store.initialize())
    [existing] = run(store.list_analyses(1, 0))["items"]
    saved = run(store.save_analyses([existing]))
    assert saved == {"inserted": [], "skipped": 1}


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda state: state.update(schema_version=2), "unsupported schema"),
        (lambda state: state.pop("cursors"), "incomplete"),
        (lambda state: state["accounts"].append(state["accounts"][0]), "invalid account at index 2"),
        (lambda state: state["analyses"][0].update(category="nope"), "analysis 0 has an invalid category"),
        (lambda state: state["analyses"][0].update(analysis_id="xyz"), "analysis 0 has an invalid id"),
        (lambda state: state["analyses"].append(state["analyses"][0]), "duplicate analysis at index 4"),
        (lambda state: state["analyses"][0]["safety"].update(confidence=2), "invalid safety assessment"),
        (lambda state: state["cursors"].update({"bad id": None}), "invalid cursor"),
    ],
)
def test_refuses_a_corrupted_data_file(mutate, message):
    from app.demo.data import create_demo_state

    state = json.loads(json.dumps(create_demo_state()))
    mutate(state)
    with pytest.raises(ValueError, match=message):
        parse_state(json.dumps(state))
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_state("{nope")


def test_startup_fails_on_a_corrupted_data_file_instead_of_overwriting_it(monkeypatch, tmp_path):
    data_file = tmp_path / "ezer.json"
    data_file.write_text("{broken", encoding="utf-8")
    store = JsonPersistence(settings(monkeypatch, EZER_DATA_FILE=str(data_file)))
    with pytest.raises(ValueError, match="not valid JSON"):
        run(store.initialize())
    assert data_file.read_text(encoding="utf-8") == "{broken"


# --- magasin de jetons -------------------------------------------------------------------------------


def test_token_file_is_written_atomically_with_restricted_permissions(monkeypatch, tmp_path):
    token_file = tmp_path / "secrets" / "tokens.json"
    store = TokenStore(settings(monkeypatch, EZER_TOKEN_FILE=str(token_file)))

    async def scenario():
        assert await store.get("a") is None
        token = {
            "account_id": "a",
            "mailbox": "a@b.c",
            "refresh_token": "r1",
            "connected_at": "2026-08-27T16:19:18.536Z",
        }
        await store.save(token)
        await store.save({**token, "account_id": "b", "refresh_token": "r2", "scopes": "Mail.ReadWrite"})
        await store.rotate("a", "r3")
        await store.rotate("missing", "ignored")
        assert (await store.get("a"))["refresh_token"] == "r3"
        assert await store.remove("b") is True
        assert await store.remove("b") is False

    run(scenario())
    assert token_file.stat().st_mode & 0o777 == 0o600
    assert token_file.parent.stat().st_mode & 0o777 == 0o700
    assert [entry for entry in os.listdir(token_file.parent) if entry.endswith(".tmp")] == []
    assert [entry["account_id"] for entry in json.loads(token_file.read_text(encoding="utf-8"))["tokens"]] == ["a"]


@pytest.mark.parametrize(
    "content",
    [
        "{nope",
        "[]",
        '{"schema_version": 2, "tokens": []}',
        '{"schema_version": 1, "tokens": [{"account_id": "a"}]}',
        json.dumps(
            {
                "schema_version": 1,
                "tokens": [{"account_id": "a", "mailbox": "m", "refresh_token": "", "connected_at": "2026-08-27"}],
            }
        ),
    ],
)
def test_a_corrupted_token_file_is_refused(monkeypatch, tmp_path, content):
    token_file = tmp_path / "tokens.json"
    token_file.write_text(content, encoding="utf-8")
    store = TokenStore(settings(monkeypatch, EZER_TOKEN_FILE=str(token_file)))
    with pytest.raises(ValueError, match="token file"):
        run(store.get("a"))


def test_scope_helpers():
    assert grants_write_access("offline_access Mail.ReadWrite User.Read")
    assert grants_write_access("https://graph.microsoft.com/mail.readwrite")
    assert not grants_write_access("offline_access Mail.Read")
    assert not grants_write_access(None)
    assert refresh_scopes_for("Mail.ReadWrite profile openid User.Read", "fallback") == (
        "offline_access Mail.ReadWrite User.Read"
    )
    assert refresh_scopes_for("profile", "fallback") == "fallback"
    assert refresh_scopes_for(None, "fallback") == "fallback"


# --- garde SSRF des curseurs Graph -------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?%24skip=10",
        "https://graph.microsoft.com:443/v1.0/me/messages",
    ],
)
def test_follows_only_graph_next_links(url):
    assert trusted_graph_url(url) is not None


@pytest.mark.parametrize(
    "url",
    [
        "http://graph.microsoft.com/v1.0/me/messages",
        "https://graph.microsoft.com.evil.example/v1.0/me/messages",
        "https://evil.example/v1.0/me/messages",
        "https://graph.microsoft.com@evil.example/v1.0/me/messages",
        "https://graph.microsoft.com\\@evil.example/v1.0/me/messages",
        "https://graph.microsoft.com:8443/v1.0/me/messages",
        "https://graph.microsoft.com/beta/me/messages",
        "https://graph.microsoft.com/v1.0",
        "https://user:pw@graph.microsoft.com/v1.0/me/messages",
        "file:///etc/passwd",
        "3",
        "",
        " https://graph.microsoft.com/v1.0/me/messages",
    ],
)
def test_refuses_any_other_cursor(url):
    assert trusted_graph_url(url) is None


def test_catalog_connector_cursor_semantics():
    messages = [m for m in DEMO_MESSAGES if m.account_id == "gmail-primary"]

    async def load():
        return DEMO_MESSAGES

    connector = CatalogConnector(Account(id="gmail-primary", provider="gmail"), load)

    async def scenario():
        first = await connector.fetch(None, 3)
        assert [m.provider_message_id for m in first.messages] == ["gmail-001", "gmail-002", "gmail-003"]
        assert first.next_cursor == "3" and not first.cursor_reset
        rest = await connector.fetch("3", 10)
        assert [m.provider_message_id for m in rest.messages] == ["gmail-004"]
        assert rest.next_cursor == str(len(messages))
        reset = await connector.fetch("99", 2)
        assert reset.cursor_reset and reset.next_cursor == "2"
        bogus = await connector.fetch("https://x", 2)
        assert bogus.cursor_reset

    run(scenario())


def test_javascript_rounding_goes_toward_positive_infinity_on_ties():
    assert [js_round(value) for value in (0.5, 1.5, 2.5, -0.5, -1.5)] == [1, 2, 3, 0, -1]

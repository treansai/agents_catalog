from __future__ import annotations

import json
import logging

from ezer.connectors.base import AuthenticationError
from ezer.observability import JsonFormatter, exception_leaves, safe_exception_fields


def test_json_formatter_ignores_unapproved_content_fields() -> None:
    record = logging.LogRecord(
        name="ezer.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="sync_complete",
        args=(),
        exc_info=None,
    )
    record.event = "sync_complete"
    record.body_text = "secret email body"
    record.access_token = "oauth-secret"  # noqa: S105 - inert leak-detection fixture
    record.provider = "gmail"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "sync_complete"
    assert payload["provider"] == "gmail"
    assert "secret email body" not in json.dumps(payload)
    assert "oauth-secret" not in json.dumps(payload)


def test_json_formatter_does_not_render_third_party_message_prose() -> None:
    record = logging.LogRecord(
        name="third.party",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="failed request with Authorization: Bearer oauth-secret",
        args=(),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "external_log"
    assert "oauth-secret" not in json.dumps(payload)


def test_exception_group_diagnostics_are_machine_safe_and_account_is_hashed() -> None:
    error = AuthenticationError(
        provider="outlook",
        account_id="private-mailbox",
        operation="oauth_token",
        code="invalid_request_aadsts9002346",
    )
    grouped = ExceptionGroup("provider prose is not logged", [error])

    assert exception_leaves(grouped) == (error,)
    fields = safe_exception_fields(error)
    assert fields == {
        "account_id_hash": "3f22969b29d8fe09",
        "error_code": "invalid_request_aadsts9002346",
        "error_type": "AuthenticationError",
        "operation": "oauth_token",
        "provider": "outlook",
    }
    assert "private-mailbox" not in json.dumps(fields)

"""Command-line interface for API, worker, one-shot sync, and diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Protocol, cast

import uvicorn

from ezer import __version__
from ezer.api import create_app
from ezer.checkpoints import open_checkpointer
from ezer.config import (
    ClientCredentialsAuth,
    MsalDeviceCodeAuth,
    OutlookAccount,
    Settings,
    is_personal_microsoft_mailbox,
)
from ezer.connectors.auth import normalize_microsoft_device_login_uri
from ezer.connectors.base import ConnectorConfigurationError
from ezer.connectors.outlook import validate_outlook_account_configuration
from ezer.domain import SyncReport
from ezer.observability import (
    configure_logging,
    exception_leaves,
    log_event,
    safe_exception_fields,
)
from ezer.persistence import persistence_from_settings
from ezer.worker import run_worker, sync_once

logger = logging.getLogger(__name__)

_DEVICE_USER_CODE = re.compile(r"^[A-Za-z0-9-]{4,32}$")


class DeviceCodeChallenge(Protocol):
    """Safe subset of a device-code challenge that may be shown to the operator."""

    @property
    def verification_uri(self) -> str: ...

    @property
    def user_code(self) -> str: ...


ChallengeCallback = Callable[[DeviceCodeChallenge], Awaitable[None] | None]


class InteractiveOutlookTokenProvider(Protocol):
    async def authenticate_interactively(
        self,
        on_challenge: ChallengeCallback,
    ) -> object: ...


class DeviceCodeProviderFactory(Protocol):
    def __call__(
        self,
        *,
        account_id: str,
        mailbox: str,
        client_id: str,
        cache_path: Path,
        cache_encryption_key: bytes | None,
    ) -> InteractiveOutlookTokenProvider: ...


def _port(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 65_535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return parsed


def _positive_limit(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("limit must be positive")
    return parsed


def _device_code_provider(
    *,
    account_id: str,
    mailbox: str,
    client_id: str,
    cache_path: Path,
    cache_encryption_key: bytes | None,
) -> InteractiveOutlookTokenProvider:
    """Import MSAL only for the explicit interactive authentication command."""

    from ezer.connectors.msal_auth import MsalDeviceCodeTokenProvider

    return cast(
        InteractiveOutlookTokenProvider,
        MsalDeviceCodeTokenProvider(
            account_id=account_id,
            mailbox=mailbox,
            client_id=client_id,
            cache_path=cache_path,
            cache_encryption_key=cache_encryption_key,
        ),
    )


def _auth_configuration_error(account_id: str, code: str) -> ConnectorConfigurationError:
    return ConnectorConfigurationError(
        provider="outlook",
        account_id=account_id,
        operation="oauth_config",
        code=code,
    )


def _device_code_cache_key(settings: Settings, account_id: str) -> bytes | None:
    key = (
        settings.msal_cache_aes_key.get_secret_value().encode("utf-8")
        if settings.msal_cache_aes_key is not None
        else None
    )
    if key is not None and len(key) not in (16, 24, 32):
        raise _auth_configuration_error(account_id, "invalid_msal_cache_encryption_key")
    if settings.environment == "production":
        if not settings.msal_cache_dir.is_absolute():
            raise _auth_configuration_error(account_id, "msal_cache_path_must_be_absolute")
        if key is None:
            raise _auth_configuration_error(account_id, "msal_cache_encryption_required")
    return key


async def authenticate_outlook(
    settings: Settings,
    account_id: str,
    *,
    provider_factory: DeviceCodeProviderFactory | None = None,
) -> None:
    """Authenticate one configured Outlook account without opening the application runtime."""

    account = next(
        (
            candidate
            for candidate in settings.load_accounts()
            if candidate.id == account_id and isinstance(candidate, OutlookAccount)
        ),
        None,
    )
    if account is None:
        raise _auth_configuration_error(account_id, "outlook_account_not_configured")

    auth = account.auth
    if isinstance(auth, MsalDeviceCodeAuth):
        client_id = auth.client_id
    elif isinstance(auth, ClientCredentialsAuth) and is_personal_microsoft_mailbox(account.mailbox):
        # Compatibility for existing personal-account configuration. The client secret and
        # tenant are intentionally ignored; device-code authentication is a public-client flow.
        client_id = auth.client_id
    else:
        raise _auth_configuration_error(account_id, "device_code_auth_not_configured")

    make_provider = provider_factory or _device_code_provider
    provider = make_provider(
        account_id=account.id,
        mailbox=account.mailbox,
        client_id=client_id,
        cache_path=settings.msal_cache_dir / f"{account.id}.bin",
        cache_encryption_key=_device_code_cache_key(settings, account.id),
    )

    async def show_challenge(challenge: DeviceCodeChallenge) -> None:
        verification_uri = normalize_microsoft_device_login_uri(challenge.verification_uri)
        user_code = challenge.user_code.strip()
        if verification_uri is None or _DEVICE_USER_CODE.fullmatch(user_code) is None:
            raise _auth_configuration_error(account.id, "invalid_device_code_challenge")
        print(
            f"Open {verification_uri} and enter code {user_code}.",
            file=sys.stderr,
            flush=True,
        )

    await provider.authenticate_interactively(show_challenge)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ezer",
        description="Read-only Gmail and Outlook intelligence with LangGraph",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    api_parser = commands.add_parser("api", help="run the HTTP API")
    api_parser.add_argument("--host", default="127.0.0.1")
    api_parser.add_argument("--port", default=8080, type=_port)

    worker_parser = commands.add_parser("worker", help="poll configured mailboxes")
    worker_parser.add_argument("--once", action="store_true", help="run one polling round")

    sync_parser = commands.add_parser("sync", help="synchronize one provider page")
    sync_parser.add_argument(
        "--account",
        action="append",
        dest="account_ids",
        metavar="ID",
        help="configured account ID; repeat to select multiple accounts",
    )
    sync_parser.add_argument("--limit", type=_positive_limit)

    auth_parser = commands.add_parser("auth", help="authenticate an email provider")
    auth_providers = auth_parser.add_subparsers(dest="auth_provider", required=True)
    outlook_auth_parser = auth_providers.add_parser(
        "outlook",
        help="authenticate a personal Microsoft mailbox with device code",
    )
    outlook_auth_parser.add_argument(
        "--account",
        required=True,
        metavar="ID",
        help="configured Outlook account ID",
    )

    commands.add_parser("doctor", help="validate configuration and persistence")
    return parser


def _safe_reports(reports: Sequence[SyncReport]) -> str:
    payload = [report.model_dump(mode="json", exclude={"analyses"}) for report in reports]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


async def doctor(settings: Settings) -> None:
    """Check configuration and storage without contacting mail or model providers."""

    settings.validate_for_role("sync")
    for account in settings.load_accounts():
        if isinstance(account, OutlookAccount):
            validate_outlook_account_configuration(account)
    persistence = persistence_from_settings(settings)
    try:
        await persistence.open()
        if not await persistence.health():
            raise RuntimeError("persistence health check failed")
        async with open_checkpointer(settings):
            pass
    finally:
        await persistence.close()


def _run_api(settings: Settings, arguments: argparse.Namespace) -> None:
    settings.validate_for_role("api")
    uvicorn.run(
        create_app(settings),
        host=str(arguments.host),
        port=int(arguments.port),
        log_config=None,
        access_log=False,
        server_header=False,
        proxy_headers=False,
        timeout_graceful_shutdown=30,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run a command and return a process-compatible exit status."""

    arguments = build_parser().parse_args(argv)
    configure_logging("INFO")
    try:
        settings = Settings()
        configure_logging(settings.log_level)

        if arguments.command == "api":
            _run_api(settings, arguments)
        elif arguments.command == "worker":
            reports = asyncio.run(run_worker(settings, once=bool(arguments.once)))
            if arguments.once:
                print(_safe_reports(reports))
        elif arguments.command == "sync":
            reports = asyncio.run(
                sync_once(
                    settings,
                    account_ids=arguments.account_ids,
                    limit=arguments.limit,
                )
            )
            print(_safe_reports(reports))
        elif arguments.command == "auth":
            if arguments.auth_provider != "outlook":  # pragma: no cover - argparse enforces it
                raise AssertionError("unreachable auth provider")
            asyncio.run(authenticate_outlook(settings, str(arguments.account)))
            print(
                json.dumps(
                    {
                        "status": "authenticated",
                        "provider": "outlook",
                        "account_id": str(arguments.account),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        elif arguments.command == "doctor":
            asyncio.run(doctor(settings))
            print('{"status":"ok"}')
        else:  # pragma: no cover - argparse enforces the command set
            raise AssertionError("unreachable command")
    except KeyboardInterrupt:
        log_event(logger, "command_interrupted", status="interrupted")
        return 130
    except Exception as exc:
        leaves = exception_leaves(exc)
        for leaf in leaves:
            log_event(
                logger,
                "command_error",
                status="failed",
                **safe_exception_fields(leaf),
            )
        log_event(
            logger,
            "command_failed",
            status="failed",
            error_type=type(exc).__name__,
            failure_count=len(leaves),
        )
        return 1
    return 0


__all__ = ["authenticate_outlook", "build_parser", "doctor", "main"]

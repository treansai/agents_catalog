"""Authentification déléguée Microsoft par device code, entièrement côté backend.

Le navigateur de l'utilisateur ne reçoit que l'URI publique et le code à saisir ; le device code,
les jetons d'accès et le refresh token restent dans ce processus et dans le magasin de jetons.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import Settings
from app.domain.models import Account, ConnectionState, ConnectionStatus
from app.mail.errors import MailConnectionError
from app.mail.token_store import TokenStore
from app.services.http_fetch import HttpFetch
from app.services.timeutil import format_instant

logger = logging.getLogger("ezer.outlook")

Sleeper = Callable[[float], Awaitable[None]]

# Autorité grand public : les boîtes outlook.com/hotmail ne vivent dans aucun tenant Entra.
AUTHORITY = "https://login.microsoftonline.com/consumers"
DEVICE_CODE_ENDPOINT = f"{AUTHORITY}/oauth2/v2.0/devicecode"
TOKEN_ENDPOINT = f"{AUTHORITY}/oauth2/v2.0/token"
GRAPH_ME_ENDPOINT = "https://graph.microsoft.com/v1.0/me"
# Mail.ReadWrite couvre la lecture et le déplacement vers la corbeille demandé par l'assistant.
SCOPES = "offline_access Mail.ReadWrite User.Read"
WRITE_SCOPE = "mail.readwrite"
DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

# La page à ouvrir dépend de l'autorité : Entra renvoie /devicelogin, l'autorité grand public
# renvoie /link. L'URI de Microsoft est utilisée telle quelle après contrôle de cette liste.
DEVICE_LOGIN_URIS = frozenset(
    {
        "https://microsoft.com/devicelogin",
        "https://www.microsoft.com/devicelogin",
        "https://microsoft.com/link",
        "https://www.microsoft.com/link",
    }
)

# Seules ces réponses signifient que le refresh token est définitivement mort. Toute autre erreur
# (réseau, indisponibilité, portée refusée) laisse la connexion en place : jeter un jeton encore
# valable imposerait à l'utilisateur une reconnexion qu'aucun incident ne justifie.
DEAD_GRANT_ERRORS = frozenset({"invalid_grant", "invalid_client", "unauthorized_client"})

# Microsoft renvoie les portées accordées sans `offline_access` et parfois avec des portées OIDC
# (`profile` seule est refusée : elle exige `openid`). Un renouvellement ne rejoue donc pas la
# chaîne telle quelle : il garde les portées de ressource consenties et remet `offline_access`.
OIDC_SCOPES = frozenset({"openid", "profile", "email", "offline_access"})

USER_CODE = re.compile(r"[A-Za-z0-9-]{4,32}")
REQUEST_TIMEOUT_S = 15.0
MAX_RESPONSE_CHARS = 256 * 1024
MIN_POLL_INTERVAL_MS = 1_000
MAX_POLL_INTERVAL_MS = 60_000
MAX_FLOW_LIFETIME_MS = 20 * 60 * 1_000
ACCESS_TOKEN_SAFETY_MARGIN_MS = 60_000


def refresh_scopes_for(granted_scopes: str | None, fallback: str) -> str:
    granted = [scope for scope in (granted_scopes or "").split() if scope != "" and scope.lower() not in OIDC_SCOPES]
    if not granted:
        return fallback
    return " ".join(["offline_access", *granted])


def grants_write_access(scopes: str | None) -> bool:
    """Un jeton consenti avant l'élargissement des portées ne peut pas supprimer."""
    if scopes is None:
        return False
    return any(scope.strip().lower().endswith(WRITE_SCOPE) for scope in scopes.split())


def _now_ms() -> float:
    return time.time() * 1000


def _normalize_device_login_uri(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 2_048:
        return None
    trimmed = value.strip().rstrip("/")
    return trimmed if trimmed in DEVICE_LOGIN_URIS else None


def _positive_seconds(value: Any, fallback: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value <= 0:
        return fallback
    return min(value, maximum)


async def real_sleep(milliseconds: float) -> None:
    await asyncio.sleep(milliseconds / 1000)


@dataclass
class DeviceCodeFlow:
    status: ConnectionStatus
    code: str | None
    verification_uri: str
    user_code: str
    expires_at: float


@dataclass
class CachedAccessToken:
    value: str
    expires_at: float


class OutlookAuth:
    def __init__(
        self, settings: Settings, tokens: TokenStore, http_fetch: HttpFetch, sleeper: Sleeper = real_sleep
    ) -> None:
        self._settings = settings
        self._tokens = tokens
        self._fetch = http_fetch
        self._sleep = sleeper
        self._flows: dict[str, DeviceCodeFlow] = {}
        self._access_tokens: dict[str, CachedAccessToken] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def aclose(self) -> None:
        """Un flux en attente ne doit jamais empêcher l'arrêt du processus."""
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def assert_connectable(self, account: Account) -> tuple[str, str]:
        """Vérifie qu'un compte peut être connecté, sans révéler la configuration au client."""
        if account.provider != "outlook":
            raise MailConnectionError("provider_not_supported")
        if account.mailbox is None or account.mailbox == "":
            raise MailConnectionError("mailbox_not_configured")
        if self._settings.outlook_client_id is None:
            raise MailConnectionError("client_not_configured")
        return account.mailbox, self._settings.outlook_client_id

    async def state(self, account: Account) -> ConnectionState:
        stored = await self._tokens.get(account.id)
        flow = self._current_flow(account.id)
        write_enabled = grants_write_access(stored.get("scopes") if stored else None)
        common: dict[str, Any] = {
            "account_id": account.id,
            "provider": account.provider,
            "mailbox": account.mailbox,
            "write_enabled": write_enabled,
        }
        if stored is not None and (flow is None or flow.status != "pending"):
            return ConnectionState(
                **common,
                status="connected",
                code=None,
                verification_uri=None,
                user_code=None,
                expires_at=None,
                connected_at=stored["connected_at"],
            )
        if flow is None:
            return ConnectionState(
                **common,
                status="disconnected",
                code=None,
                verification_uri=None,
                user_code=None,
                expires_at=None,
                connected_at=None,
            )
        pending = flow.status == "pending"
        return ConnectionState(
            **common,
            status=flow.status,
            code=flow.code,
            verification_uri=flow.verification_uri if pending else None,
            user_code=flow.user_code if pending else None,
            expires_at=format_instant_ms(flow.expires_at) if pending else None,
            connected_at=stored["connected_at"] if stored else None,
        )

    async def connect(self, account: Account) -> ConnectionState:
        """Démarre un flux device code, ou renvoie celui déjà en cours pour ce compte.

        La complétion est poursuivie en tâche de fond : la requête HTTP retourne dès que le code est
        connu.
        """
        mailbox, client_id = self.assert_connectable(account)
        pending = self._current_flow(account.id)
        if pending is not None and pending.status == "pending":
            return await self.state(account)

        payload = await self._post_form(DEVICE_CODE_ENDPOINT, {"client_id": client_id, "scope": SCOPES})
        verification = payload.get("verification_uri")
        if verification is None:
            verification = payload.get("verification_url")
        verification_uri = _normalize_device_login_uri(verification)
        raw_user_code = payload.get("user_code")
        user_code = raw_user_code.strip() if isinstance(raw_user_code, str) else ""
        raw_device_code = payload.get("device_code")
        device_code = raw_device_code if isinstance(raw_device_code, str) else ""
        if verification_uri is None or not USER_CODE.fullmatch(user_code) or device_code == "":
            raise MailConnectionError(
                "public_client_flow_not_enabled" if payload.get("error") == "invalid_client" else "device_flow_failed"
            )

        lifetime_ms = _positive_seconds(payload.get("expires_in"), 900, 1_800) * 1_000
        flow = DeviceCodeFlow(
            status="pending",
            code=None,
            verification_uri=verification_uri,
            user_code=user_code,
            expires_at=_now_ms() + min(lifetime_ms, MAX_FLOW_LIFETIME_MS),
        )
        self._flows[account.id] = flow

        interval_ms = min(
            max(_positive_seconds(payload.get("interval"), 5, 60) * 1_000, MIN_POLL_INTERVAL_MS),
            MAX_POLL_INTERVAL_MS,
        )
        task = asyncio.create_task(self._complete_flow(account.id, mailbox, client_id, device_code, flow, interval_ms))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return await self.state(account)

    async def disconnect(self, account: Account) -> bool:
        self._flows.pop(account.id, None)
        self._access_tokens.pop(account.id, None)
        return await self._tokens.remove(account.id)

    async def access_token_for(self, account: Account) -> str:
        """Jeton d'accès Graph pour un compte connecté ; échange le refresh token si nécessaire."""
        cached = self._access_tokens.get(account.id)
        if cached is not None and cached.expires_at > _now_ms():
            return cached.value

        _, client_id = self.assert_connectable(account)
        stored = await self._tokens.get(account.id)
        if stored is None:
            raise MailConnectionError("account_not_connected")

        try:
            payload = await self._post_form(
                TOKEN_ENDPOINT,
                {
                    "client_id": client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": stored["refresh_token"],
                    # Renouveler sur les portées réellement consenties, pas sur celles que demande la
                    # version courante du code : élargir SCOPES ne doit jamais invalider une connexion.
                    "scope": refresh_scopes_for(stored.get("scopes"), SCOPES),
                },
            )
        except MailConnectionError:
            raise
        except Exception as error:
            raise MailConnectionError("token_refresh_failed") from error

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or access_token == "":
            error_code = payload.get("error")
            provider_error = error_code.lower() if isinstance(error_code, str) else ""
            if provider_error not in DEAD_GRANT_ERRORS:
                # La connexion est conservée : l'échec peut être passager et le jeton rester utilisable.
                raise MailConnectionError("token_refresh_failed")
            await self._tokens.remove(account.id)
            raise MailConnectionError("reauthentication_required")
        rotated = payload.get("refresh_token")
        if isinstance(rotated, str) and rotated != "":
            await self._tokens.rotate(account.id, rotated)
        lifetime_ms = _positive_seconds(payload.get("expires_in"), 3_600, 86_400) * 1_000
        self._access_tokens[account.id] = CachedAccessToken(
            value=access_token,
            expires_at=_now_ms() + max(lifetime_ms - ACCESS_TOKEN_SAFETY_MARGIN_MS, 0),
        )
        return access_token

    def _current_flow(self, account_id: str) -> DeviceCodeFlow | None:
        flow = self._flows.get(account_id)
        if flow is None:
            return None
        if flow.status == "pending" and flow.expires_at <= _now_ms():
            flow.status = "failed"
            flow.code = "device_code_expired"
        return flow

    async def _complete_flow(
        self,
        account_id: str,
        mailbox: str,
        client_id: str,
        device_code: str,
        flow: DeviceCodeFlow,
        initial_interval_ms: float,
    ) -> None:
        interval_ms = initial_interval_ms
        try:
            while flow.status == "pending":
                if flow.expires_at <= _now_ms():
                    raise MailConnectionError("device_code_expired")
                await self._sleep(interval_ms)
                if self._flows.get(account_id) is not flow:
                    return

                payload = await self._post_form(
                    TOKEN_ENDPOINT,
                    {"client_id": client_id, "grant_type": DEVICE_CODE_GRANT, "device_code": device_code},
                )
                error = payload.get("error")
                error = error if isinstance(error, str) else ""
                if error == "authorization_pending":
                    continue
                if error == "slow_down":
                    interval_ms = min(interval_ms + 5_000, MAX_POLL_INTERVAL_MS)
                    continue
                if error != "":
                    raise MailConnectionError(
                        error if error in ("authorization_declined", "expired_token") else "device_flow_failed"
                    )

                access_token = payload.get("access_token")
                refresh_token = payload.get("refresh_token")
                if not isinstance(access_token, str) or access_token == "":
                    raise MailConnectionError("device_flow_failed")
                if not isinstance(refresh_token, str) or refresh_token == "":
                    # Sans refresh token la connexion ne survivrait pas à une heure : la refuser est plus
                    # honnête qu'un compte qui se déconnecte seul.
                    raise MailConnectionError("offline_access_denied")

                signed_in = await self._signed_in_mailbox(access_token)
                if signed_in != mailbox:
                    # L'adresse principale d'un compte Microsoft diffère souvent de l'alias saisi. Elle
                    # est tracée ici, pour l'opérateur seul : l'API ne renvoie que `mailbox_mismatch`.
                    logger.warning(
                        json.dumps(
                            {
                                "event": "connection_mailbox_mismatch",
                                "account_id": account_id,
                                "configured_mailbox": mailbox,
                                "signed_in_mailbox": signed_in,
                            },
                            ensure_ascii=False,
                        )
                    )
                    raise MailConnectionError("mailbox_mismatch")

                scope = payload.get("scope")
                await self._tokens.save(
                    {
                        "account_id": account_id,
                        "mailbox": mailbox,
                        "refresh_token": refresh_token,
                        "connected_at": format_instant_ms(_now_ms()),
                        "scopes": scope[:2_048] if isinstance(scope, str) else "",
                    }
                )
                lifetime_ms = _positive_seconds(payload.get("expires_in"), 3_600, 86_400) * 1_000
                self._access_tokens[account_id] = CachedAccessToken(
                    value=access_token,
                    expires_at=_now_ms() + max(lifetime_ms - ACCESS_TOKEN_SAFETY_MARGIN_MS, 0),
                )
                flow.status = "connected"
                flow.code = None
                return
        except Exception as error:
            if self._flows.get(account_id) is not flow:
                return
            flow.status = "failed"
            flow.code = error.code if isinstance(error, MailConnectionError) else "device_flow_failed"

    async def _signed_in_mailbox(self, access_token: str) -> str:
        try:
            response = await self._fetch(
                GRAPH_ME_ENDPOINT,
                method="GET",
                headers={"Accept": "application/json", "Authorization": f"Bearer {access_token}"},
                timeout=REQUEST_TIMEOUT_S,
            )
        except Exception as error:
            raise MailConnectionError("microsoft_unreachable") from error
        if not response.is_success:
            raise MailConnectionError("mailbox_lookup_failed")
        payload = self._read_json(response)
        if not isinstance(payload, dict):
            raise MailConnectionError("mailbox_lookup_failed")
        address = payload.get("mail")
        if address is None:
            address = payload.get("userPrincipalName")
        if not isinstance(address, str) or len(address) > 320:
            raise MailConnectionError("mailbox_lookup_failed")
        return address.strip().lower()

    async def _post_form(self, endpoint: str, fields: dict[str, str]) -> dict[str, Any]:
        try:
            response = await self._fetch(
                endpoint,
                method="POST",
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                body=urlencode(fields),
                timeout=REQUEST_TIMEOUT_S,
            )
        except Exception as error:
            raise MailConnectionError("microsoft_unreachable") from error
        # Un refus OAuth utilise un 400 porteur d'un corps JSON exploitable ; seul un 5xx est opaque.
        if response.status_code >= 500:
            raise MailConnectionError("microsoft_unavailable")
        payload = self._read_json(response)
        if not isinstance(payload, dict):
            raise MailConnectionError("device_flow_failed")
        return payload

    @staticmethod
    def _read_json(response: httpx.Response) -> Any:
        raw = response.text
        if len(raw) > MAX_RESPONSE_CHARS:
            raise MailConnectionError("microsoft_response_too_large")
        try:
            return json.loads(raw, parse_constant=_reject_constant)
        except (ValueError, RecursionError) as error:
            raise MailConnectionError("microsoft_response_invalid") from error


def _reject_constant(name: str) -> Any:
    raise ValueError(f"invalid JSON constant {name}")


def format_instant_ms(milliseconds: float) -> str:
    return format_instant(datetime.fromtimestamp(milliseconds / 1000, UTC))

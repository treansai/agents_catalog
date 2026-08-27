"""Delegated Microsoft consumer authentication backed by a persistent MSAL cache."""

from __future__ import annotations

import asyncio
import fcntl
import importlib
import inspect
import math
import os
import re
import stat
import tempfile
import time
from collections.abc import Awaitable, Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast

from ezer.connectors.auth import (
    AUTHORITY,
    BearerToken,
    normalize_microsoft_device_login_uri,
)
from ezer.connectors.base import AuthenticationError

_SCOPES = ("Mail.Read",)

_CACHE_MAGIC = b"EZERMSAL1"
_CACHE_PLAINTEXT = b"P"
_CACHE_AES_GCM = b"G"
_AES_GCM_NONCE_BYTES = 12
_AES_GCM_TAG_BYTES = 16
_MAX_CACHE_BYTES = 8 * 1024 * 1024
_MAX_ACCESS_TOKEN_BYTES = 256 * 1024
_MAX_TOKEN_LIFETIME_SECONDS = 86_400.0
_SAFE_DEVICE_CODE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_INTERACTION_REQUIRED_ERRORS = frozenset(
    {
        "consent_required",
        "interaction_required",
        "invalid_grant",
        "no_tokens_found",
    }
)
_PUBLIC_CLIENT_NOT_ENABLED_ERROR_CODE = 70002


class _SerializableTokenCache(Protocol):
    has_state_changed: bool

    def deserialize(self, state: str | None) -> None: ...

    def serialize(self) -> str: ...


class _PublicClientApplication(Protocol):
    def get_accounts(self, username: str | None = None) -> Sequence[Mapping[str, Any]]: ...

    def acquire_token_silent_with_error(
        self,
        scopes: list[str],
        account: Mapping[str, Any],
        *,
        force_refresh: bool = False,
    ) -> Mapping[str, Any] | None: ...

    def initiate_device_flow(self, *, scopes: list[str]) -> Mapping[str, Any]: ...

    def acquire_token_by_device_flow(self, flow: dict[str, Any]) -> Mapping[str, Any]: ...


class MsalClientFactory(Protocol):
    def __call__(
        self,
        client_id: str,
        *,
        authority: str,
        token_cache: _SerializableTokenCache,
    ) -> _PublicClientApplication: ...


class _AesGcmCipher(Protocol):
    def update(self, data: bytes) -> None: ...

    def encrypt_and_digest(self, plaintext: bytes) -> tuple[bytes, bytes]: ...

    def decrypt_and_verify(self, ciphertext: bytes, tag: bytes) -> bytes: ...


class _AesModule(Protocol):
    MODE_GCM: int

    def new(
        self,
        key: bytes,
        mode: int,
        *,
        nonce: bytes,
        mac_len: int,
    ) -> _AesGcmCipher: ...


def _default_client_factory(
    client_id: str,
    *,
    authority: str,
    token_cache: _SerializableTokenCache,
) -> _PublicClientApplication:
    module = importlib.import_module("msal")
    factory = module.__dict__["PublicClientApplication"]
    return cast(
        _PublicClientApplication,
        factory(client_id, authority=authority, token_cache=token_cache),
    )


@dataclass(slots=True)
class _DeviceFlowSession:
    cache: _SerializableTokenCache
    client: _PublicClientApplication
    flow: dict[str, Any]
    user_code: str
    verification_uri: str


@dataclass(frozen=True, slots=True)
class DeviceCodeChallenge:
    """The bounded, non-secret instructions an operator needs to complete sign-in."""

    verification_uri: str
    user_code: str


class MsalDeviceCodeTokenProvider:
    """Acquire delegated ``Mail.Read`` tokens for one personal Microsoft mailbox.

    ``get_token`` is deliberately silent. An operator must explicitly call
    ``authenticate_interactively`` when the cache has no usable token.
    """

    def __init__(
        self,
        account_id: str,
        mailbox: str,
        client_id: str,
        cache_path: str | os.PathLike[str],
        cache_encryption_key: bytes | None = None,
        client_factory: MsalClientFactory = _default_client_factory,
    ) -> None:
        normalized_account_id = account_id.strip()
        normalized_mailbox = mailbox.strip().casefold()
        normalized_client_id = client_id.strip()
        if not normalized_account_id or len(normalized_account_id) > 128:
            raise ValueError("account_id must contain between 1 and 128 characters")
        if (
            not normalized_mailbox
            or len(normalized_mailbox) > 320
            or normalized_mailbox.count("@") != 1
        ):
            raise ValueError("mailbox must be a valid email address")
        if not normalized_client_id or len(normalized_client_id) > 256:
            raise ValueError("client_id must contain between 1 and 256 characters")
        if cache_encryption_key is not None and len(cache_encryption_key) not in {16, 24, 32}:
            raise ValueError("cache_encryption_key must be a 16, 24, or 32 byte AES key")

        path = Path(cache_path)
        if not path.name:
            raise ValueError("cache_path must identify a file")

        self._account_id = normalized_account_id
        self._mailbox = normalized_mailbox
        self._client_id = normalized_client_id
        self._cache_path = path
        self._lock_path = path.with_name(f"{path.name}.lock")
        self._cache_encryption_key = (
            bytes(cache_encryption_key) if cache_encryption_key is not None else None
        )
        self._client_factory = client_factory
        self._lock = asyncio.Lock()
        self._cached: BearerToken | None = None
        self._force_refresh = False
        self._associated_data = b"\0".join(
            (
                b"ezer-msal-cache-v1",
                normalized_account_id.encode("utf-8"),
                normalized_mailbox.encode("utf-8"),
                normalized_client_id.encode("utf-8"),
            )
        )

    async def get_token(self) -> BearerToken:
        """Return a cached or silently refreshed token without prompting a user."""

        cached = self._cached
        if cached is not None and not self._force_refresh and cached.is_valid(time.monotonic()):
            return cached

        async with self._lock:
            cached = self._cached
            if cached is not None and not self._force_refresh and cached.is_valid(time.monotonic()):
                return cached
            force_refresh = self._force_refresh
            token = await asyncio.to_thread(self._acquire_silent, force_refresh)
            self._cached = token
            self._force_refresh = False
            return token

    async def invalidate(self, token: str | None = None) -> None:
        """Force MSAL to redeem its refresh token after a bearer rejection."""

        async with self._lock:
            if token is None or (self._cached is not None and self._cached.value == token):
                self._cached = None
                self._force_refresh = True

    async def authenticate_interactively(
        self,
        on_challenge: Callable[[DeviceCodeChallenge], Awaitable[None] | None],
    ) -> BearerToken:
        """Run an explicit device-code flow and persist its resulting MSAL cache."""

        async with self._lock:
            session = await asyncio.to_thread(self._initiate_device_flow)
            notified = on_challenge(
                DeviceCodeChallenge(
                    verification_uri=session.verification_uri,
                    user_code=session.user_code,
                )
            )
            if inspect.isawaitable(notified):
                await notified
            try:
                token = await asyncio.to_thread(self._complete_device_flow, session)
            except asyncio.CancelledError:
                session.flow["expires_at"] = 0
                raise
            self._cached = token
            self._force_refresh = False
            return token

    def _acquire_silent(self, force_refresh: bool) -> BearerToken:
        with self._exclusive_cache_lock():
            # Avoid authority discovery and produce the actionable local error when an operator
            # has not run the explicit login command yet.
            if self._read_cache_file() is None:
                raise self._authentication_error("msal_silent", "device_code_login_required")
            cache, client = self._load_client()
            accounts = self._matching_accounts(client)
            if not accounts:
                raise self._authentication_error("msal_silent", "device_code_login_required")
            try:
                result = client.acquire_token_silent_with_error(
                    list(_SCOPES),
                    accounts[0],
                    force_refresh=force_refresh,
                )
            except Exception:
                raise self._authentication_error("msal_silent", "msal_client_failure") from None
            self._persist_if_changed(cache)
            return self._token_from_result(result, operation="msal_silent")

    def _initiate_device_flow(self) -> _DeviceFlowSession:
        with self._exclusive_cache_lock():
            cache, client = self._load_client()
            try:
                flow = client.initiate_device_flow(scopes=list(_SCOPES))
            except Exception:
                raise self._authentication_error(
                    "msal_device_flow",
                    "device_flow_initialization_failed",
                ) from None
            user_code = flow.get("user_code") if isinstance(flow, Mapping) else None
            if not isinstance(user_code, str) or _SAFE_DEVICE_CODE.fullmatch(user_code) is None:
                raise self._authentication_error(
                    "msal_device_flow",
                    self._device_flow_initialization_error(flow),
                )
            verification_uri = normalize_microsoft_device_login_uri(flow.get("verification_uri"))
            if verification_uri is None:
                raise self._authentication_error(
                    "msal_device_flow",
                    "unexpected_device_login_uri",
                )
            return _DeviceFlowSession(
                cache=cache,
                client=client,
                flow=dict(flow),
                user_code=user_code,
                verification_uri=verification_uri,
            )

    @staticmethod
    def _device_flow_initialization_error(flow: object) -> str:
        """Reduce the one actionable Entra registration error to a fixed safe code."""

        if not isinstance(flow, Mapping) or flow.get("error") != "invalid_client":
            return "device_flow_initialization_failed"
        error_codes = flow.get("error_codes")
        if isinstance(error_codes, Sequence) and not isinstance(error_codes, str | bytes):
            if any(
                isinstance(code, int)
                and not isinstance(code, bool)
                and code == _PUBLIC_CLIENT_NOT_ENABLED_ERROR_CODE
                for code in error_codes
            ):
                return "public_client_flow_not_enabled"
        return "device_flow_initialization_failed"

    def _complete_device_flow(self, session: _DeviceFlowSession) -> BearerToken:
        with self._exclusive_cache_lock():
            self._deserialize_current_cache(session.cache)
            try:
                result = session.client.acquire_token_by_device_flow(session.flow)
            except Exception:
                raise self._authentication_error(
                    "msal_device_flow",
                    "device_flow_authentication_failed",
                ) from None
            token = self._token_from_result(result, operation="msal_device_flow")
            if not self._matching_accounts(session.client):
                raise self._authentication_error(
                    "msal_device_flow",
                    "device_flow_account_mismatch",
                )
            self._persist_if_changed(session.cache)
            return token

    def _load_client(self) -> tuple[_SerializableTokenCache, _PublicClientApplication]:
        cache = self._new_cache()
        self._deserialize_current_cache(cache)
        try:
            client = self._client_factory(
                self._client_id,
                authority=AUTHORITY,
                token_cache=cache,
            )
        except AuthenticationError:
            raise
        except Exception:
            raise self._authentication_error("msal_config", "msal_client_unavailable") from None
        return cache, client

    def _new_cache(self) -> _SerializableTokenCache:
        try:
            module: ModuleType = importlib.import_module("msal")
            factory = module.__dict__["SerializableTokenCache"]
            cache = factory()
        except Exception:
            raise self._authentication_error("msal_config", "msal_dependency_unavailable") from None
        if not callable(getattr(cache, "deserialize", None)) or not callable(
            getattr(cache, "serialize", None)
        ):
            raise self._authentication_error("msal_config", "msal_dependency_unavailable")
        return cast(_SerializableTokenCache, cache)

    def _matching_accounts(
        self,
        client: _PublicClientApplication,
    ) -> list[Mapping[str, Any]]:
        try:
            accounts = client.get_accounts(username=self._mailbox)
        except Exception:
            raise self._authentication_error("msal_silent", "msal_client_failure") from None
        matches: list[Mapping[str, Any]] = []
        for account in accounts:
            username = account.get("username") if isinstance(account, Mapping) else None
            if isinstance(username, str) and username.strip().casefold() == self._mailbox:
                matches.append(account)
        return matches

    def _token_from_result(
        self,
        result: Mapping[str, Any] | None,
        *,
        operation: str,
    ) -> BearerToken:
        if result is None:
            raise self._authentication_error(operation, "device_code_login_required")
        if not isinstance(result, Mapping):
            raise self._authentication_error(operation, "invalid_msal_response")
        access_token = result.get("access_token")
        if isinstance(access_token, str) and access_token:
            if len(access_token.encode("utf-8")) > _MAX_ACCESS_TOKEN_BYTES:
                raise self._authentication_error(operation, "invalid_msal_response")
            expires_in_raw = result.get("expires_in", 0)
            try:
                expires_in = float(expires_in_raw)
            except TypeError, ValueError:
                expires_in = 0.0
            if not math.isfinite(expires_in):
                expires_in = 0.0
            expires_in = min(max(expires_in, 0.0), _MAX_TOKEN_LIFETIME_SECONDS)
            return BearerToken(access_token, time.monotonic() + expires_in)

        provider_error = result.get("error")
        normalized_error = provider_error.casefold() if isinstance(provider_error, str) else ""
        if normalized_error in _INTERACTION_REQUIRED_ERRORS:
            code = "device_code_login_required"
        else:
            code = "msal_authentication_failed"
        raise self._authentication_error(operation, code)

    def _deserialize_current_cache(self, cache: _SerializableTokenCache) -> None:
        encoded = self._read_cache_file()
        if encoded is None:
            state: str | None = None
        else:
            state = self._decode_cache(encoded)
        try:
            cache.deserialize(state)
        except Exception:
            raise self._authentication_error("msal_cache", "token_cache_corrupt") from None

    def _persist_if_changed(self, cache: _SerializableTokenCache) -> None:
        if not cache.has_state_changed:
            return
        try:
            serialized = cache.serialize()
        except Exception:
            raise self._authentication_error(
                "msal_cache",
                "token_cache_serialization_failed",
            ) from None
        if not isinstance(serialized, str):
            raise self._authentication_error("msal_cache", "token_cache_serialization_failed")
        self._atomic_write(self._encode_cache(serialized))

    def _encode_cache(self, state: str) -> bytes:
        plaintext = state.encode("utf-8")
        if len(plaintext) > _MAX_CACHE_BYTES:
            raise self._authentication_error("msal_cache", "token_cache_too_large")
        if self._cache_encryption_key is None:
            return _CACHE_MAGIC + _CACHE_PLAINTEXT + plaintext
        try:
            aes = cast(_AesModule, importlib.import_module("Crypto.Cipher.AES"))
            nonce = os.urandom(_AES_GCM_NONCE_BYTES)
            cipher = aes.new(
                self._cache_encryption_key,
                aes.MODE_GCM,
                nonce=nonce,
                mac_len=_AES_GCM_TAG_BYTES,
            )
            cipher.update(self._associated_data)
            ciphertext, tag = cipher.encrypt_and_digest(plaintext)
        except Exception:
            raise self._authentication_error(
                "msal_cache",
                "token_cache_encryption_failed",
            ) from None
        return _CACHE_MAGIC + _CACHE_AES_GCM + nonce + tag + ciphertext

    def _decode_cache(self, encoded: bytes) -> str:
        if len(encoded) > _MAX_CACHE_BYTES + 128 or not encoded.startswith(_CACHE_MAGIC):
            raise self._authentication_error("msal_cache", "token_cache_corrupt")
        kind_offset = len(_CACHE_MAGIC)
        if len(encoded) <= kind_offset:
            raise self._authentication_error("msal_cache", "token_cache_corrupt")
        kind = encoded[kind_offset : kind_offset + 1]
        payload = encoded[kind_offset + 1 :]
        if kind == _CACHE_PLAINTEXT:
            if self._cache_encryption_key is not None:
                raise self._authentication_error("msal_cache", "token_cache_unencrypted")
            plaintext = payload
        elif kind == _CACHE_AES_GCM:
            if self._cache_encryption_key is None:
                raise self._authentication_error("msal_cache", "token_cache_encryption_required")
            minimum = _AES_GCM_NONCE_BYTES + _AES_GCM_TAG_BYTES
            if len(payload) < minimum:
                raise self._authentication_error("msal_cache", "token_cache_corrupt")
            nonce = payload[:_AES_GCM_NONCE_BYTES]
            tag = payload[_AES_GCM_NONCE_BYTES:minimum]
            ciphertext = payload[minimum:]
            try:
                aes = cast(_AesModule, importlib.import_module("Crypto.Cipher.AES"))
                cipher = aes.new(
                    self._cache_encryption_key,
                    aes.MODE_GCM,
                    nonce=nonce,
                    mac_len=_AES_GCM_TAG_BYTES,
                )
                cipher.update(self._associated_data)
                plaintext = cipher.decrypt_and_verify(ciphertext, tag)
            except Exception:
                raise self._authentication_error("msal_cache", "token_cache_corrupt") from None
        else:
            raise self._authentication_error("msal_cache", "token_cache_corrupt")
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError:
            raise self._authentication_error("msal_cache", "token_cache_corrupt") from None

    @contextmanager
    def _exclusive_cache_lock(self) -> Generator[None]:
        try:
            self._cache_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self._lock_path, flags, 0o600)
        except OSError:
            raise self._authentication_error("msal_cache", "token_cache_unavailable") from None
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        except AuthenticationError:
            raise
        except OSError:
            raise self._authentication_error("msal_cache", "token_cache_unavailable") from None
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _read_cache_file(self) -> bytes | None:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._cache_path, flags)
        except FileNotFoundError:
            return None
        except OSError:
            raise self._authentication_error("msal_cache", "token_cache_unavailable") from None
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise self._authentication_error("msal_cache", "token_cache_unavailable")
            os.fchmod(descriptor, 0o600)
            content = bytearray()
            while True:
                chunk = os.read(descriptor, min(64 * 1024, _MAX_CACHE_BYTES + 129 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > _MAX_CACHE_BYTES + 128:
                    raise self._authentication_error("msal_cache", "token_cache_too_large")
            return bytes(content)
        except AuthenticationError:
            raise
        except OSError:
            raise self._authentication_error("msal_cache", "token_cache_unavailable") from None
        finally:
            os.close(descriptor)

    def _atomic_write(self, content: bytes) -> None:
        temporary_path: str | None = None
        descriptor: int | None = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{self._cache_path.name}.",
                dir=self._cache_path.parent,
            )
            os.fchmod(descriptor, 0o600)
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short cache write")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary_path, self._cache_path)
            temporary_path = None
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_descriptor = os.open(self._cache_path.parent, directory_flags)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            raise self._authentication_error("msal_cache", "token_cache_write_failed") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass

    def _authentication_error(self, operation: str, code: str) -> AuthenticationError:
        return AuthenticationError(
            provider="outlook",
            account_id=self._account_id,
            operation=operation,
            code=code,
        )


__all__ = [
    "AUTHORITY",
    "DeviceCodeChallenge",
    "MsalClientFactory",
    "MsalDeviceCodeTokenProvider",
]

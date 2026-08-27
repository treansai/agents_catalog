from __future__ import annotations

import asyncio
import json
import stat
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar

import pytest

from ezer.connectors.base import AuthenticationError
from ezer.connectors.msal_auth import (
    AUTHORITY,
    DeviceCodeChallenge,
    MsalDeviceCodeTokenProvider,
)


class FakeSerializableTokenCache:
    constructor_threads: ClassVar[list[int]] = []

    def __init__(self) -> None:
        self.constructor_threads.append(threading.get_ident())
        self.state: dict[str, Any] = {"accounts": []}
        self.has_state_changed = False

    def deserialize(self, state: str | None) -> None:
        self.state = {"accounts": []} if state is None else json.loads(state)
        self.has_state_changed = False

    def serialize(self) -> str:
        self.has_state_changed = False
        return json.dumps(self.state, separators=(",", ":"), sort_keys=True)


class FakePublicClientApplication:
    instances: ClassVar[list[FakePublicClientApplication]] = []
    calls: ClassVar[list[tuple[str, int, dict[str, Any]]]] = []
    interactive_username: ClassVar[str] = "person@hotmail.fr"
    silent_delay: ClassVar[float] = 0.0
    silent_error: ClassVar[dict[str, Any] | None] = None
    device_flow_error: ClassVar[dict[str, Any] | None] = None
    verification_uri: ClassVar[str | None] = "https://www.microsoft.com/link"
    block_device_flow: ClassVar[bool] = False
    device_flow_started: ClassVar[threading.Event] = threading.Event()
    device_flow_stopped: ClassVar[threading.Event] = threading.Event()
    _activity_lock: ClassVar[threading.Lock] = threading.Lock()
    _active_silent: ClassVar[int] = 0
    peak_active_silent: ClassVar[int] = 0

    def __init__(
        self,
        client_id: str,
        *,
        authority: str,
        token_cache: FakeSerializableTokenCache,
    ) -> None:
        self.client_id = client_id
        self.authority = authority
        self.token_cache = token_cache
        self.instances.append(self)
        self._record("construct", client_id=client_id, authority=authority)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.calls = []
        cls.interactive_username = "person@hotmail.fr"
        cls.silent_delay = 0.0
        cls.silent_error = None
        cls.device_flow_error = None
        cls.verification_uri = "https://www.microsoft.com/link"
        cls.block_device_flow = False
        cls.device_flow_started = threading.Event()
        cls.device_flow_stopped = threading.Event()
        cls._active_silent = 0
        cls.peak_active_silent = 0
        FakeSerializableTokenCache.constructor_threads = []

    @classmethod
    def _record(cls, name: str, **details: Any) -> None:
        cls.calls.append((name, threading.get_ident(), details))

    def get_accounts(self, username: str | None = None) -> list[Mapping[str, Any]]:
        self._record("get_accounts", username=username)
        accounts = self.token_cache.state.get("accounts", [])
        if not isinstance(accounts, list):
            return []
        return [
            account
            for account in accounts
            if isinstance(account, dict)
            and isinstance(account.get("username"), str)
            and (username is None or account["username"].casefold() == username.casefold())
        ]

    def initiate_device_flow(self, *, scopes: list[str]) -> Mapping[str, Any]:
        self._record("initiate_device_flow", scopes=list(scopes))
        if self.device_flow_error is not None:
            return dict(self.device_flow_error)
        flow: dict[str, Any] = {
            "user_code": "ABCD-EFGH",
            "device_code": "provider-device-secret",
            "expires_at": time.time() + 900,
            "message": "provider prose must never leave the adapter",
        }
        if self.verification_uri is not None:
            flow["verification_uri"] = self.verification_uri
        return flow

    def acquire_token_by_device_flow(self, flow: dict[str, Any]) -> Mapping[str, Any]:
        self._record(
            "acquire_token_by_device_flow",
            received_device_code=flow.get("device_code") == "provider-device-secret",
        )
        if self.block_device_flow:
            self.device_flow_started.set()
            while float(flow.get("expires_at", 0)) > time.time():
                time.sleep(0.005)
            self.device_flow_stopped.set()
            return {
                "error": "authorization_pending",
                "error_description": "provider prose must remain internal",
            }
        self.token_cache.state = {
            "accounts": [{"username": self.interactive_username}],
            "access_token": "interactive-access-value",
            "generation": 0,
        }
        self.token_cache.has_state_changed = True
        return {"access_token": "interactive-access-value", "expires_in": 3600}

    def acquire_token_silent_with_error(
        self,
        scopes: list[str],
        account: Mapping[str, Any],
        *,
        force_refresh: bool = False,
    ) -> Mapping[str, Any] | None:
        self._record(
            "acquire_token_silent_with_error",
            scopes=list(scopes),
            account=dict(account),
            force_refresh=force_refresh,
        )
        with self._activity_lock:
            type(self)._active_silent += 1
            type(self).peak_active_silent = max(
                type(self).peak_active_silent,
                type(self)._active_silent,
            )
        try:
            if self.silent_delay:
                time.sleep(self.silent_delay)
            if self.silent_error is not None:
                return dict(self.silent_error)
            current = self.token_cache.state.get("access_token")
            if not isinstance(current, str):
                return None
            if force_refresh:
                generation = int(self.token_cache.state.get("generation", 0)) + 1
                current = f"refreshed-access-value-{generation}"
                self.token_cache.state["access_token"] = current
                self.token_cache.state["generation"] = generation
                self.token_cache.has_state_changed = True
            return {"access_token": current, "expires_in": 3600}
        finally:
            with self._activity_lock:
                type(self)._active_silent -= 1


@pytest.fixture(autouse=True)
def fake_msal(monkeypatch: pytest.MonkeyPatch) -> None:
    FakePublicClientApplication.reset()
    module = ModuleType("msal")
    module.__dict__["SerializableTokenCache"] = FakeSerializableTokenCache
    module.__dict__["PublicClientApplication"] = FakePublicClientApplication
    monkeypatch.setitem(sys.modules, "msal", module)


def provider(cache_path: Path, *, key: bytes | None = None) -> MsalDeviceCodeTokenProvider:
    return MsalDeviceCodeTokenProvider(
        "personal",
        "Person@Hotmail.FR",
        "public-client-id",
        cache_path,
        cache_encryption_key=key,
    )


async def authenticate(
    token_provider: MsalDeviceCodeTokenProvider,
) -> tuple[str, list[DeviceCodeChallenge]]:
    challenges: list[DeviceCodeChallenge] = []
    result = await token_provider.authenticate_interactively(challenges.append)
    return result.value, challenges


async def test_first_auth_is_explicit_bounded_and_runs_msal_off_loop(tmp_path: Path) -> None:
    cache_path = tmp_path / "tokens.cache"
    token_provider = provider(cache_path)
    event_loop_thread = threading.get_ident()

    with pytest.raises(AuthenticationError) as missing:
        await token_provider.get_token()
    assert missing.value.code == "device_code_login_required"
    assert FakePublicClientApplication.instances == []

    value, challenges = await authenticate(token_provider)

    assert value == "interactive-access-value"
    assert challenges == [
        DeviceCodeChallenge(
            verification_uri="https://www.microsoft.com/link",
            user_code="ABCD-EFGH",
        )
    ]
    challenge_rendering = repr(challenges[0])
    assert "provider prose" not in challenge_rendering
    assert "provider-device-secret" not in challenge_rendering
    assert all(
        instance.authority == AUTHORITY for instance in FakePublicClientApplication.instances
    )
    scope_calls = [
        details["scopes"]
        for name, _, details in FakePublicClientApplication.calls
        if name == "initiate_device_flow"
    ]
    assert scope_calls == [["Mail.Read"]]
    msal_threads = FakeSerializableTokenCache.constructor_threads + [
        thread_id for _, thread_id, _ in FakePublicClientApplication.calls
    ]
    assert msal_threads
    assert all(thread_id != event_loop_thread for thread_id in msal_threads)
    cache_metadata = await asyncio.to_thread(cache_path.stat)
    lock_metadata = await asyncio.to_thread(Path(f"{cache_path}.lock").stat)
    assert stat.S_IMODE(cache_metadata.st_mode) == 0o600
    assert stat.S_IMODE(lock_metadata.st_mode) == 0o600


async def test_public_client_registration_error_is_actionable_and_secret_free(
    tmp_path: Path,
) -> None:
    FakePublicClientApplication.device_flow_error = {
        "error": "invalid_client",
        "error_codes": [70002],
        "error_description": "provider prose with tenant and correlation identifiers",
    }

    with pytest.raises(AuthenticationError) as caught:
        await provider(tmp_path / "tokens.cache").authenticate_interactively(lambda _: None)

    assert caught.value.code == "public_client_flow_not_enabled"
    assert "provider prose" not in str(caught.value)


@pytest.mark.parametrize(
    "verification_uri",
    [
        None,
        "https://microsoft.example/devicelogin",
        "https://www.microsoft.com.evil.test/link",
        "http://www.microsoft.com/link",
    ],
)
async def test_unrecognized_device_login_page_is_refused(
    tmp_path: Path,
    verification_uri: str | None,
) -> None:
    FakePublicClientApplication.verification_uri = verification_uri

    with pytest.raises(AuthenticationError) as caught:
        await provider(tmp_path / "tokens.cache").authenticate_interactively(lambda _: None)

    assert caught.value.code == "unexpected_device_login_uri"


@pytest.mark.parametrize(
    ("returned", "expected"),
    [
        ("https://www.microsoft.com/link", "https://www.microsoft.com/link"),
        ("https://microsoft.com/link/", "https://microsoft.com/link"),
        ("https://microsoft.com/devicelogin", "https://microsoft.com/devicelogin"),
        (
            " https://www.microsoft.com/devicelogin ",
            "https://www.microsoft.com/devicelogin",
        ),
    ],
)
async def test_trusted_device_login_page_reaches_the_operator(
    tmp_path: Path,
    returned: str,
    expected: str,
) -> None:
    FakePublicClientApplication.verification_uri = returned

    _, challenges = await authenticate(provider(tmp_path / "tokens.cache"))

    assert [challenge.verification_uri for challenge in challenges] == [expected]


async def test_silent_refresh_matches_mailbox_and_persists_rotation(tmp_path: Path) -> None:
    cache_path = tmp_path / "tokens.cache"
    await authenticate(provider(cache_path))
    FakePublicClientApplication.calls = []
    token_provider = provider(cache_path)

    cached = await token_provider.get_token()
    await token_provider.invalidate(cached.value)
    refreshed = await token_provider.get_token()

    assert cached.value == "interactive-access-value"
    assert refreshed.value == "refreshed-access-value-1"
    account_calls = [
        details["username"]
        for name, _, details in FakePublicClientApplication.calls
        if name == "get_accounts"
    ]
    assert account_calls == ["person@hotmail.fr", "person@hotmail.fr"]
    silent_calls = [
        details
        for name, _, details in FakePublicClientApplication.calls
        if name == "acquire_token_silent_with_error"
    ]
    assert [call["scopes"] for call in silent_calls] == [["Mail.Read"], ["Mail.Read"]]
    assert [call["force_refresh"] for call in silent_calls] == [False, True]
    assert (await provider(cache_path).get_token()).value == "refreshed-access-value-1"


async def test_async_callback_and_concurrent_callers_are_serialized(tmp_path: Path) -> None:
    cache_path = tmp_path / "tokens.cache"
    challenges: list[DeviceCodeChallenge] = []

    async def on_challenge(challenge: DeviceCodeChallenge) -> None:
        await asyncio.sleep(0)
        challenges.append(challenge)

    await provider(cache_path).authenticate_interactively(on_challenge)
    assert len(challenges) == 1

    FakePublicClientApplication.calls = []
    FakePublicClientApplication.silent_delay = 0.03
    one_provider = provider(cache_path)
    results = await asyncio.gather(*(one_provider.get_token() for _ in range(12)))
    assert {result.value for result in results} == {"interactive-access-value"}
    assert (
        sum(
            name == "acquire_token_silent_with_error"
            for name, _, _ in FakePublicClientApplication.calls
        )
        == 1
    )

    FakePublicClientApplication.calls = []
    FakePublicClientApplication.peak_active_silent = 0
    first = provider(cache_path)
    second = provider(cache_path)
    await asyncio.gather(first.get_token(), second.get_token())
    assert FakePublicClientApplication.peak_active_silent == 1


async def test_corrupt_cache_fails_closed_without_provider_content(tmp_path: Path) -> None:
    cache_path = tmp_path / "tokens.cache"
    await asyncio.to_thread(cache_path.write_bytes, b"provider prose and token material")
    await asyncio.to_thread(cache_path.chmod, 0o644)

    with pytest.raises(AuthenticationError) as caught:
        await provider(cache_path).get_token()

    assert caught.value.code == "token_cache_corrupt"
    assert "provider prose" not in str(caught.value)
    cache_metadata = await asyncio.to_thread(cache_path.stat)
    assert stat.S_IMODE(cache_metadata.st_mode) == 0o600


async def test_aes_gcm_cache_round_trip_and_wrong_key_fail_closed(tmp_path: Path) -> None:
    cache_path = tmp_path / "tokens.cache"
    encryption_key = b"K" * 32
    value, _ = await authenticate(provider(cache_path, key=encryption_key))

    encoded = await asyncio.to_thread(cache_path.read_bytes)
    assert value.encode() not in encoded
    assert b"person@hotmail.fr" not in encoded
    assert b"provider-device-secret" not in encoded
    assert (await provider(cache_path, key=encryption_key).get_token()).value == value

    with pytest.raises(AuthenticationError) as wrong_key:
        await provider(cache_path, key=b"W" * 32).get_token()
    assert wrong_key.value.code == "token_cache_corrupt"

    with pytest.raises(AuthenticationError) as missing_key:
        await provider(cache_path).get_token()
    assert missing_key.value.code == "token_cache_encryption_required"


@pytest.mark.parametrize(
    ("provider_error", "expected_code"),
    [
        ("interaction_required", "device_code_login_required"),
        ("prose_with_person_identifier", "msal_authentication_failed"),
    ],
)
async def test_provider_error_is_reduced_to_safe_machine_code(
    tmp_path: Path,
    provider_error: str,
    expected_code: str,
) -> None:
    cache_path = tmp_path / "tokens.cache"
    await authenticate(provider(cache_path))
    FakePublicClientApplication.silent_error = {
        "error": provider_error,
        "error_description": "provider prose with account and correlation details",
    }

    with pytest.raises(AuthenticationError) as caught:
        await provider(cache_path).get_token()

    assert caught.value.code == expected_code
    assert "provider prose" not in str(caught.value)


async def test_cancellation_expires_internal_flow_and_stops_polling_thread(tmp_path: Path) -> None:
    FakePublicClientApplication.block_device_flow = True
    token_provider = provider(tmp_path / "tokens.cache")
    task = asyncio.create_task(token_provider.authenticate_interactively(lambda challenge: None))
    started = await asyncio.to_thread(FakePublicClientApplication.device_flow_started.wait, 1.0)
    assert started

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    stopped = await asyncio.to_thread(FakePublicClientApplication.device_flow_stopped.wait, 1.0)
    assert stopped

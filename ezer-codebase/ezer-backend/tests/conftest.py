from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

API_KEY = "test-api-key"


def json_response(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Aucune variable EZER_* héritée, et un répertoire courant jetable (pas de .env, pas de data/)."""
    for name in list(os.environ):
        if name.startswith("EZER_") or name == "TYPESAFE_API_KEY":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def start_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., TestClient]]:
    """Démarre l'application complète (lifespan compris) avec l'environnement donné."""
    stack = ExitStack()

    def start(environment: dict[str, str | None], **options: Any) -> TestClient:
        for name, value in environment.items():
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        application = create_app(Settings(_env_file=None), **options)  # type: ignore[call-arg]
        return stack.enter_context(TestClient(application))

    yield start
    stack.close()


@pytest.fixture
def auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}

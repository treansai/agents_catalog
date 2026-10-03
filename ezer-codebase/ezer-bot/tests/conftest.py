import pytest

EZER_VARIABLES = (
    "EZER_API_KEY",
    "EZER_HOST",
    "EZER_PORT",
    "EZER_ANTHROPIC_API_KEY",
    "EZER_ANTHROPIC_MODEL",
    "EZER_LLM_MAX_TOKENS",
    "EZER_LLM_TIMEOUT_SECONDS",
    "EZER_BACKEND_URL",
    "EZER_BACKEND_API_KEY",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in EZER_VARIABLES:
        monkeypatch.delenv(name, raising=False)

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel

import ezer.agents as agent_module
from ezer.agents import AgentResponseError, AnthropicAgentRuntime
from ezer.config import Settings
from ezer.domain import SafetyAssessment


class _FakeStructuredChain:
    def __init__(self, response: object) -> None:
        self.response = response

    async def ainvoke(self, messages: Sequence[BaseMessage]) -> object:
        assert messages
        return self.response


def _raw(stop_reason: str = "end_turn") -> AIMessage:
    return AIMessage(content="", response_metadata={"stop_reason": stop_reason})


async def test_structured_response_is_validated() -> None:
    expected = SafetyAssessment(
        risk_level="low",
        phishing_likelihood=0.1,
        rationale="No strong indicator.",
        confidence=0.9,
    )
    chain = _FakeStructuredChain(
        {"raw": _raw(), "parsed": expected.model_dump(), "parsing_error": None}
    )

    result = await AnthropicAgentRuntime._invoke_structured(
        cast(Any, chain),
        SafetyAssessment,
        [HumanMessage(content="untrusted")],
        operation="safety",
    )

    assert result == expected


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ({"raw": _raw("refusal"), "parsed": None}, "model_refusal"),
        ({"raw": _raw("max_tokens"), "parsed": None}, "truncated_at_max_tokens"),
        (
            {"raw": _raw(), "parsed": None, "parsing_error": ValueError("hostile text")},
            "schema_parsing_failed",
        ),
        ({"raw": _raw(), "parsed": None, "parsing_error": None}, "missing_parsed_output"),
    ],
)
async def test_invalid_model_response_raises_content_free_error(
    response: dict[str, object],
    reason: str,
) -> None:
    chain = _FakeStructuredChain(response)

    with pytest.raises(AgentResponseError) as caught:
        await AnthropicAgentRuntime._invoke_structured(
            cast(Any, chain),
            SafetyAssessment,
            [HumanMessage(content="untrusted")],
            operation="safety",
        )

    assert caught.value.reason == reason
    assert "hostile text" not in str(caught.value)


def test_sonnet_runtime_omits_sampling_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    constructor_calls: list[dict[str, object]] = []
    structured_calls: list[tuple[type[BaseModel], dict[str, object]]] = []

    class FakeChatAnthropic:
        def __init__(self, **kwargs: object) -> None:
            constructor_calls.append(kwargs)

        def with_structured_output(
            self,
            schema: type[BaseModel],
            **kwargs: object,
        ) -> _FakeStructuredChain:
            structured_calls.append((schema, kwargs))
            return _FakeStructuredChain({})

    monkeypatch.setattr(agent_module, "ChatAnthropic", FakeChatAnthropic)
    runtime = AnthropicAgentRuntime(
        Settings(_env_file=None, anthropic_api_key="test-key", anthropic_model="claude-sonnet-5")
    )

    assert runtime.model_id == "claude-sonnet-5"
    assert len(constructor_calls) == 2
    assert {call["effort"] for call in constructor_calls} == {"low", "medium"}
    for call in constructor_calls:
        assert call["model_name"] == "claude-sonnet-5"
        assert call["max_tokens_to_sample"] == 4096
        assert call["thinking"] == {"type": "adaptive"}
        assert call["max_retries"] == 0
        assert {"temperature", "top_p", "top_k"}.isdisjoint(call)
    assert len(structured_calls) == 4
    assert all(
        options == {"method": "json_schema", "include_raw": True} for _, options in structured_calls
    )

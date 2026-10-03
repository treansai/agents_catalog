"""ChatModel abstraction over the Anthropic Messages API (injectable for tests)."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from anthropic import AsyncAnthropic

from app.assistant.tools import ALL_TOOL_SCHEMAS


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str


@dataclass
class ChatTurn:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    raw_content: list[dict[str, Any]] | None = None


class ChatModel(Protocol):
    async def invoke(self, messages: list[ChatTurn]) -> ChatTurn: ...


@dataclass
class AnthropicModelConfig:
    anthropic_model: str
    llm_max_tokens: int
    llm_timeout_seconds: float
    anthropic_api_key: str | None = None


ChatModelFactory = Callable[..., ChatModel]
"""``factory(config, tools=bool)``: ``tools=True`` builds the orchestrator, ``False`` a sub-agent."""


def _as_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text_of(blocks: list[Any]) -> str:
    parts = [block.text for block in blocks if getattr(block, "type", None) == "text"]
    return "\n".join(parts).strip()


def to_anthropic_messages(turns: list[ChatTurn]) -> tuple[str, list[dict[str, Any]]]:
    system = "\n\n".join(turn.content for turn in turns if turn.role == "system")
    messages: list[dict[str, Any]] = []
    for turn in turns:
        if turn.role == "system":
            continue
        if turn.role == "user":
            messages.append({"role": "user", "content": turn.content})
            continue
        if turn.role == "assistant":
            if turn.raw_content is not None:
                messages.append({"role": "assistant", "content": turn.raw_content})
                continue
            if turn.tool_calls:
                content: list[dict[str, Any]] = []
                if turn.content != "":
                    content.append({"type": "text", "text": turn.content})
                for call in turn.tool_calls:
                    content.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.args})
                messages.append({"role": "assistant", "content": content})
                continue
            messages.append({"role": "assistant", "content": turn.content})
            continue
        block = {
            "type": "tool_result",
            "tool_use_id": turn.tool_call_id or "",
            "content": turn.content,
        }
        last = messages[-1] if messages else None
        if last is not None and last["role"] == "user" and isinstance(last["content"], list):
            last["content"].append(block)
        else:
            messages.append({"role": "user", "content": [block]})
    return system, messages


class AnthropicChatModel:
    def __init__(self, config: AnthropicModelConfig, *, tools: bool) -> None:
        if config.anthropic_api_key is None:
            raise RuntimeError("EZER_ANTHROPIC_API_KEY is required to create the assistant")
        self._config = config
        self._tools = tools
        self._client = AsyncAnthropic(
            api_key=config.anthropic_api_key,
            timeout=float(config.llm_timeout_seconds),
            max_retries=0,
        )

    async def invoke(self, messages: list[ChatTurn]) -> ChatTurn:
        system, api_messages = to_anthropic_messages(messages)
        arguments: dict[str, Any] = {
            "model": self._config.anthropic_model,
            "max_tokens": self._config.llm_max_tokens,
            "messages": api_messages,
        }
        if system:
            arguments["system"] = system
        if self._tools:
            arguments["tools"] = ALL_TOOL_SCHEMAS
        response = await self._client.messages.create(**arguments)
        tool_calls = [
            ToolCall(name=block.name, args=_as_record(block.input), id=block.id)
            for block in response.content
            if block.type == "tool_use"
        ]
        return ChatTurn(
            role="assistant",
            content=_text_of(response.content),
            tool_calls=tool_calls,
            raw_content=[block.model_dump(mode="json", exclude_none=True) for block in response.content],
        )


def create_anthropic_chat_model(config: Any, *, tools: bool) -> ChatModel:
    return AnthropicChatModel(config, tools=tools)

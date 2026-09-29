import type { ContentBlock, MessageParam } from "@anthropic-ai/sdk/resources/messages";
import Anthropic from "@anthropic-ai/sdk";

import { ALL_TOOL_SCHEMAS } from "./tools";

export interface ToolCall {
  name: string;
  args: Record<string, unknown>;
  id: string;
}

export interface ChatTurn {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  toolCalls?: ToolCall[];
  toolCallId?: string;
  rawContent?: ContentBlock[];
}

export interface ChatModel {
  invoke(messages: ChatTurn[]): Promise<ChatTurn>;
}

export type ChatModelFactory = (config: AnthropicModelConfig, options: { tools: boolean }) => ChatModel;

export interface AnthropicModelConfig {
  anthropicApiKey?: string;
  anthropicModel: string;
  llmMaxTokens: number;
  llmTimeoutSeconds: number;
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}

function textOf(blocks: ContentBlock[]): string {
  const parts: string[] = [];
  for (const block of blocks) {
    if (block.type === "text") parts.push(block.text);
  }
  return parts.join("\n").trim();
}

function toAnthropicMessages(turns: ChatTurn[]): { system: string; messages: MessageParam[] } {
  const system = turns
    .filter((turn) => turn.role === "system")
    .map((turn) => turn.content)
    .join("\n\n");
  const messages: MessageParam[] = [];
  for (const turn of turns) {
    if (turn.role === "system") continue;
    if (turn.role === "user") {
      messages.push({ role: "user", content: turn.content });
      continue;
    }
    if (turn.role === "assistant") {
      if (turn.rawContent !== undefined) {
        messages.push({ role: "assistant", content: turn.rawContent });
        continue;
      }
      if (turn.toolCalls !== undefined && turn.toolCalls.length > 0) {
        const content: MessageParam["content"] = [];
        if (turn.content !== "") {
          content.push({ type: "text", text: turn.content });
        }
        for (const call of turn.toolCalls) {
          content.push({ type: "tool_use", id: call.id, name: call.name, input: call.args });
        }
        messages.push({ role: "assistant", content });
        continue;
      }
      messages.push({ role: "assistant", content: turn.content });
      continue;
    }
    const block = {
      type: "tool_result" as const,
      tool_use_id: turn.toolCallId ?? "",
      content: turn.content
    };
    const last = messages[messages.length - 1];
    if (last !== undefined && last.role === "user" && Array.isArray(last.content)) {
      last.content.push(block);
    } else {
      messages.push({ role: "user", content: [block] });
    }
  }
  return { system, messages };
}

export function createAnthropicChatModel(
  config: AnthropicModelConfig,
  options: { tools: boolean }
): ChatModel {
  if (config.anthropicApiKey === undefined) {
    throw new Error("EZER_ANTHROPIC_API_KEY is required to create the assistant");
  }
  const client = new Anthropic({
    apiKey: config.anthropicApiKey,
    timeout: config.llmTimeoutSeconds * 1000,
    maxRetries: 0
  });
  return {
    async invoke(messages: ChatTurn[]): Promise<ChatTurn> {
      const { system, messages: apiMessages } = toAnthropicMessages(messages);
      const response = await client.messages.create({
        model: config.anthropicModel,
        max_tokens: config.llmMaxTokens,
        system,
        messages: apiMessages,
        ...(options.tools ? { tools: ALL_TOOL_SCHEMAS } : {})
      });
      const toolCalls: ToolCall[] = [];
      for (const block of response.content) {
        if (block.type === "tool_use") {
          toolCalls.push({ name: block.name, args: asRecord(block.input), id: block.id });
        }
      }
      return {
        role: "assistant",
        content: textOf(response.content),
        toolCalls,
        rawContent: response.content
      };
    }
  };
}

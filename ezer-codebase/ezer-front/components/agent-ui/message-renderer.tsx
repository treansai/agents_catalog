"use client";

import { renderSafeMarkdown } from "@/lib/agent-ui/markdown";
import type { ChatMessage } from "@/lib/agent-ui/contracts";
import { AgentComponentRenderer } from "./component-renderer";

export function AgentMessageRenderer({
  message,
  workspaceId,
  conversationId,
  onAction,
}: {
  message: ChatMessage;
  workspaceId: string;
  conversationId: string;
  onAction: (actionId: string, values: Record<string, unknown>, message: ChatMessage) => void;
}) {
  if (message.kind === "text") {
    const parts = renderSafeMarkdown(message.content);
    return (
      <p className="ezc-fallback" style={{ whiteSpace: "pre-wrap" }}>
        {parts.map((part, index) =>
          part.type === "link" && part.href !== undefined ? (
            <a className="ezc-link" href={part.href} key={`${part.href}-${index}`} rel="noreferrer noopener" target="_blank">
              {part.text}
            </a>
          ) : (
            <span key={index}>{part.text}</span>
          ),
        )}
      </p>
    );
  }
  if (message.kind === "ui.patch" || message.kind === "ui.remove") {
    return null;
  }
  return (
    <AgentComponentRenderer
      conversationId={conversationId}
      messageId={message.id}
      onAction={(actionId, values) => onAction(actionId, values, message)}
      specification={message.ui}
      workspaceId={workspaceId}
    />
  );
}

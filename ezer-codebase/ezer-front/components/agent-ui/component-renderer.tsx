"use client";

import { lazy, Suspense, useEffect, useMemo, useState, type ReactElement } from "react";

import type { AgentComponentProps } from "@/components/library/agent-props";
import { EmptyStateView, SkeletonView, StatusBanner } from "@/components/library/feedback";
import {
  AgentUiClientError,
  type DataLoadState,
  type UiRenderSpec,
  validateAgainst,
} from "@/lib/agent-ui/contracts";
import { looksLikeCode } from "@/lib/agent-ui/markdown";
import { AGENT_COMPONENT_REGISTRY, requireRegisteredComponent } from "@/lib/agent-ui/registry";
import { emitAgentUiEvent } from "@/lib/agent-ui/telemetry";
import { AgentUiErrorBoundary } from "./error-boundary";

const LAZY_BY_ID = new Map(
  AGENT_COMPONENT_REGISTRY.map((entry) => [entry.id, lazy(entry.load)] as const),
);

async function fetchResolved(
  spec: UiRenderSpec,
  workspaceId: string,
  signal: AbortSignal,
  inputOverride?: Record<string, unknown>,
): Promise<{ status: string; data: unknown }> {
  const resolverInput =
    spec.data && spec.data.mode === "resolver" ? { ...spec.data.input, ...inputOverride } : {};
  const response = await fetch(
    `/api/agent-ui/resolve?workspace_id=${encodeURIComponent(workspaceId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        resolverId: spec.data && spec.data.mode === "resolver" ? spec.data.resolverId : "",
        input: resolverInput,
        componentId: spec.componentId,
        instanceId: spec.instanceId,
      }),
      signal,
    },
  );
  if (response.status === 403) {
    throw new AgentUiClientError("permission_denied", "permission_denied");
  }
  if (!response.ok) {
    throw new AgentUiClientError("invalid_payload", "resolve failed");
  }
  return (await response.json()) as { status: string; data: unknown };
}

function isEmptyPage(value: unknown): boolean {
  return (
    value !== null &&
    typeof value === "object" &&
    Array.isArray((value as { items?: unknown }).items) &&
    (value as { items: unknown[] }).items.length === 0
  );
}

export function AgentComponentRenderer({
  specification,
  messageId,
  workspaceId,
  conversationId,
  onAction,
}: {
  specification: UiRenderSpec;
  messageId: string;
  workspaceId: string;
  conversationId: string;
  onAction: AgentComponentProps["onAction"];
}): ReactElement {
  const [remoteStatus, setRemoteStatus] = useState<DataLoadState>("loading");
  const [remoteData, setRemoteData] = useState<unknown>(undefined);
  const [pageInput, setPageInput] = useState<Record<string, unknown>>({});

  const definition = useMemo(() => {
    try {
      return requireRegisteredComponent(specification.componentId, specification.componentVersion);
    } catch (error) {
      emitAgentUiEvent({
        event: "component_validation_failed",
        traceId: conversationId,
        instanceId: specification.instanceId,
        componentId: specification.componentId,
        componentVersion: specification.componentVersion,
        status: "denied",
        code: error instanceof AgentUiClientError ? error.code : "unknown_component",
      });
      return null;
    }
  }, [specification.componentId, specification.componentVersion, specification.instanceId, conversationId]);

  // Table module-level de React.lazy — jamais import(cheminAgent).
  const LazyComponent = definition === null ? null : (LAZY_BY_ID.get(definition.id) ?? null);

  const local = useMemo((): {
    status: DataLoadState;
    data: unknown;
    errorText: string | null;
    resolver: boolean;
  } => {
    if (definition === null) {
      return { status: "error", data: undefined, errorText: specification.fallbackText, resolver: false };
    }
    try {
      validateAgainst(definition.propsSchema, specification.props);
    } catch {
      emitAgentUiEvent({
        event: "component_validation_failed",
        traceId: conversationId,
        instanceId: specification.instanceId,
        componentId: specification.componentId,
        status: "error",
        code: "invalid_payload",
      });
      return { status: "error", data: undefined, errorText: specification.fallbackText, resolver: false };
    }
    if (specification.data?.mode === "inline") {
      if (looksLikeCode(JSON.stringify(specification.data.value))) {
        return { status: "error", data: undefined, errorText: specification.fallbackText, resolver: false };
      }
      try {
        validateAgainst(definition.dataSchema, specification.data.value);
        return {
          status: isEmptyPage(specification.data.value) ? "empty" : "success",
          data: specification.data.value,
          errorText: null,
          resolver: false,
        };
      } catch {
        return { status: "error", data: undefined, errorText: specification.fallbackText, resolver: false };
      }
    }
    if (specification.data?.mode !== "resolver") {
      return { status: "success", data: undefined, errorText: null, resolver: false };
    }
    if (!definition.allowedDataResolvers.includes(specification.data.resolverId)) {
      emitAgentUiEvent({
        event: "component_validation_failed",
        traceId: conversationId,
        instanceId: specification.instanceId,
        componentId: specification.componentId,
        resolverId: specification.data.resolverId,
        status: "denied",
        code: "unknown_resolver",
      });
      return { status: "error", data: undefined, errorText: specification.fallbackText, resolver: false };
    }
    return { status: "loading", data: undefined, errorText: null, resolver: true };
  }, [definition, specification, conversationId]);

  useEffect(() => {
    if (definition === null || !local.resolver || specification.data === undefined || specification.data.mode !== "resolver") {
      return;
    }
    const resolverId = specification.data.resolverId;
    const controller = new AbortController();
    emitAgentUiEvent({
      event: "component_render_started",
      traceId: conversationId,
      instanceId: specification.instanceId,
      componentId: specification.componentId,
      componentVersion: specification.componentVersion,
      status: "ok",
    });
    emitAgentUiEvent({
      event: "data_resolver_started",
      traceId: conversationId,
      instanceId: specification.instanceId,
      componentId: specification.componentId,
      resolverId,
      status: "ok",
    });
    void fetchResolved(specification, workspaceId, controller.signal, pageInput)
      .then((payload) => {
        validateAgainst(definition.dataSchema, payload.data);
        setRemoteData(payload.data);
        setRemoteStatus(payload.status === "empty" ? "empty" : "success");
        emitAgentUiEvent({
          event: "data_resolver_succeeded",
          traceId: conversationId,
          instanceId: specification.instanceId,
          componentId: specification.componentId,
          resolverId,
          status: "ok",
        });
        emitAgentUiEvent({
          event: "component_render_succeeded",
          traceId: conversationId,
          instanceId: specification.instanceId,
          componentId: specification.componentId,
          status: "ok",
        });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof AgentUiClientError && error.code === "permission_denied") {
          setRemoteStatus("permission_denied");
        } else {
          setRemoteStatus("error");
        }
        emitAgentUiEvent({
          event: "data_resolver_failed",
          traceId: conversationId,
          instanceId: specification.instanceId,
          componentId: specification.componentId,
          status: "error",
        });
      });
    return () => controller.abort();
  }, [definition, specification, workspaceId, conversationId, pageInput, local.resolver]);

  const status = local.resolver ? remoteStatus : local.status;
  const data = local.resolver ? remoteData : local.data;
  const errorText = local.errorText;

  if (definition === null || LazyComponent === undefined || LazyComponent === null) {
    return <p className="ezc-fallback">{specification.fallbackText}</p>;
  }
  if (status === "loading" || status === "idle") {
    return <SkeletonView />;
  }
  if (status === "empty") {
    return <EmptyStateView title="Aucun résultat" detail={specification.fallbackText} />;
  }
  if (status === "permission_denied") {
    return (
      <StatusBanner
        title="Permission refusée"
        detail="Cette vue n'est pas accessible dans ce workspace."
        tone="danger"
      />
    );
  }
  if (status === "error") {
    return <p className="ezc-fallback">{errorText ?? specification.fallbackText}</p>;
  }

  const componentProps: AgentComponentProps = {
    instanceId: specification.instanceId,
    messageId,
    componentId: specification.componentId,
    componentVersion: specification.componentVersion,
    props: specification.props,
    data,
    dataStatus: status,
    onAction: (actionId, values) => {
      if (actionId === "table.page") {
        setPageInput((current) => ({
          ...current,
          offset: values.offset,
          limit: values.limit,
        }));
        setRemoteStatus("loading");
      }
      onAction(actionId, values);
    },
  };

  return (
    <AgentUiErrorBoundary
      fallback={<p className="ezc-fallback">{specification.fallbackText}</p>}
      onError={() =>
        emitAgentUiEvent({
          event: "component_render_failed",
          traceId: conversationId,
          instanceId: specification.instanceId,
          componentId: specification.componentId,
          status: "error",
        })
      }
    >
      <Suspense fallback={<SkeletonView />}>
        {/* Lookup statique : le composant vient de LAZY_BY_ID, jamais d'un import dynamique agent. */}
        {/* eslint-disable-next-line react-hooks/static-components -- registry is module-scoped */}
        <LazyComponent {...componentProps} />
      </Suspense>
    </AgentUiErrorBoundary>
  );
}

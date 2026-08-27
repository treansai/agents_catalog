export type AgentUiErrorCode =
  | "unknown_component"
  | "unknown_resolver"
  | "unknown_action"
  | "invalid_payload"
  | "invalid_props"
  | "component_version_mismatch"
  | "payload_too_large"
  | "permission_denied"
  | "workspace_mismatch"
  | "confirmation_required"
  | "confirmation_invalid"
  | "timeout"
  | "resolver_failed";

export class AgentUiError extends Error {
  constructor(
    readonly code: AgentUiErrorCode,
    message: string,
    readonly status: number
  ) {
    super(message);
    this.name = "AgentUiError";
  }
}

export function agentUiError(code: AgentUiErrorCode, status: number, message: string = code): AgentUiError {
  return new AgentUiError(code, message, status);
}

export interface ConfirmationChallenge {
  confirmationId: string;
  action: string;
  target: string;
  impact: string;
  reversible: boolean;
  token: string;
}

export class ConfirmationRequiredError extends AgentUiError {
  constructor(readonly confirmation: ConfirmationChallenge) {
    super("confirmation_required", "confirmation_required", 409);
    this.name = "ConfirmationRequiredError";
  }
}

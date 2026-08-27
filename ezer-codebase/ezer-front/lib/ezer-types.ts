export type MailProvider = "gmail" | "outlook";

export type DashboardMode = "live" | "demo";
export type HealthStatus = "ready" | "unavailable";

export type EmailCategory =
  | "action_required"
  | "informational"
  | "newsletter"
  | "receipt"
  | "security"
  | "spam"
  | "other";

export type EmailPriority = "low" | "normal" | "high" | "critical";
export type Priority = EmailPriority;
export type RiskLevel = "none" | "low" | "medium" | "high";

export interface AccountSummary {
  id: string;
  provider: MailProvider;
}

export interface ActionItem {
  description: string;
  owner: string | null;
  due_date: string | null;
  confidence: number;
}

export interface SafetyAssessment {
  risk_level: RiskLevel;
  prompt_injection_detected: boolean;
  phishing_likelihood: number;
  indicators: string[];
  rationale: string;
  confidence: number;
}

export interface TriageResult {
  category: EmailCategory;
  priority: EmailPriority;
  needs_human_review: boolean;
  confidence: number;
  rationale: string;
}

/** JSON representation returned by the Ezer API. */
export interface EmailAnalysis {
  analysis_id: string;
  message_ref: string;
  content_hash: string;
  pipeline_version: string;
  model_id: string;
  prompt_version: string;
  created_at: string;
  category: EmailCategory;
  priority: EmailPriority;
  needs_human_review: boolean;
  summary: string;
  key_points: string[];
  action_items: ActionItem[];
  safety: SafetyAssessment;
  triage: TriageResult | null;
  detected_language: string;
}

export interface AnalysesPage {
  items: EmailAnalysis[];
  total: number;
  limit: number;
  offset: number;
}

export interface DashboardSnapshot {
  mode: DashboardMode;
  health: HealthStatus;
  accounts: AccountSummary[];
  analyses: EmailAnalysis[];
  total: number;
  generatedAt: string;
}

export interface SyncRequest {
  account_ids?: string[];
  limit?: number;
}

export interface SyncReport {
  account_id: string;
  provider: MailProvider;
  fetched: number;
  processed: number;
  skipped: number;
  failed: number;
  dead_lettered: number;
  cursor_advanced: boolean;
  cursor_reset: boolean;
  analyses: EmailAnalysis[];
}

export interface SyncResponse {
  reports: SyncReport[];
}

/** Commandes que la voix peut déclencher sur le tableau de bord. */
export type VoiceAction =
  | "sync"
  | "set_view_filter"
  | "set_account_filter"
  | "set_search"
  | "refresh"
  | "none";

export interface VoiceIntent {
  action: VoiceAction;
  /** Identifiant de compte, ou "all", pour sync et set_account_filter. */
  account_id: string | null;
  /** Vue demandée pour set_view_filter. */
  view_filter: "all" | "focus" | "actions" | "risk" | null;
  /** Termes de recherche pour set_search. */
  search: string | null;
  /** Phrase courte, en français, à afficher à l'utilisateur. */
  reply: string;
}

export interface VoiceCommandResponse {
  transcript: string;
  intent: VoiceIntent;
}

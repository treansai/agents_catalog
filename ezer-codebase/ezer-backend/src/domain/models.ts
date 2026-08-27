export const PROVIDERS = ["gmail", "outlook"] as const;
export type Provider = (typeof PROVIDERS)[number];

export const EMAIL_CATEGORIES = [
  "action_required",
  "informational",
  "newsletter",
  "receipt",
  "security",
  "spam",
  "other"
] as const;
export type EmailCategory = (typeof EMAIL_CATEGORIES)[number];

export const PRIORITIES = ["low", "normal", "high", "critical"] as const;
export type Priority = (typeof PRIORITIES)[number];

export const RISK_LEVELS = ["none", "low", "medium", "high"] as const;
export type RiskLevel = (typeof RISK_LEVELS)[number];

export interface Account {
  id: string;
  provider: Provider;
  /** Adresse de la boîte, requise pour connecter un compte Outlook réel. */
  mailbox?: string;
}

/** État de la connexion déléguée d'un compte auprès de son fournisseur. */
export const CONNECTION_STATUSES = ["disconnected", "pending", "connected", "failed"] as const;
export type ConnectionStatus = (typeof CONNECTION_STATUSES)[number];

/** Vue publique d'un compte : identité, boîte et état de connexion, jamais de jeton. */
export interface AccountSummary {
  id: string;
  provider: Provider;
  mailbox: string | null;
  status: ConnectionStatus;
  connected_at: string | null;
  /** Vrai quand le consentement couvre la suppression (Mail.ReadWrite). */
  write_enabled: boolean;
}

export interface ConnectionState {
  account_id: string;
  provider: Provider;
  mailbox: string | null;
  status: ConnectionStatus;
  /** Code d'erreur stable, jamais un message du fournisseur. */
  code: string | null;
  verification_uri: string | null;
  user_code: string | null;
  expires_at: string | null;
  connected_at: string | null;
  write_enabled: boolean;
}

export interface MailMessage {
  account_id: string;
  provider: Provider;
  provider_message_id: string;
  thread_id?: string | null;
  subject: string;
  sender_name: string;
  sender_address: string;
  received_at: string;
  body_text: string;
  snippet: string;
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
  priority: Priority;
  needs_human_review: boolean;
  confidence: number;
  rationale: string;
}

export interface EmailAnalysis {
  analysis_id: string;
  message_ref: string;
  content_hash: string;
  pipeline_version: string;
  model_id: string;
  prompt_version: string;
  created_at: string;
  category: EmailCategory;
  priority: Priority;
  needs_human_review: boolean;
  summary: string;
  key_points: string[];
  action_items: ActionItem[];
  safety: SafetyAssessment;
  triage: TriageResult;
  detected_language: string;
}

export interface SyncReport {
  account_id: string;
  provider: Provider;
  fetched: number;
  processed: number;
  skipped: number;
  failed: number;
  dead_lettered: number;
  cursor_advanced: boolean;
  cursor_reset: boolean;
  analyses: EmailAnalysis[];
}

export interface AnalysisFilters {
  accountId?: string;
  category?: EmailCategory;
  priority?: Priority;
  needsHumanReview?: boolean;
}

export interface PersistedState {
  schema_version: 1;
  accounts: Account[];
  analyses: EmailAnalysis[];
  cursors: Record<string, string | null>;
}

export interface FetchBatch {
  messages: MailMessage[];
  next_cursor: string | null;
  cursor_reset: boolean;
}

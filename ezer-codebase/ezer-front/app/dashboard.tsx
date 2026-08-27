"use client";

import { useCallback, useMemo, useRef, useState, type SVGProps } from "react";

import { AssistantPanel } from "./assistant-panel";
import { ConnectMailbox } from "./connect-mailbox";
import type {
  DashboardSnapshot,
  EmailAnalysis,
  EmailCategory,
  Priority,
  SyncResponse,
  VoiceCommandResponse,
  VoiceIntent,
} from "@/lib/ezer-types";
import {
  startRecording,
  VoiceCaptureError,
  type VoiceRecording,
} from "@/lib/voice-capture";

type IconName =
  | "arrow"
  | "check"
  | "chevron"
  | "inbox"
  | "lock"
  | "mic"
  | "refresh"
  | "search"
  | "shield"
  | "spark"
  | "warning";

type ViewFilter = "all" | "focus" | "actions" | "risk";

const categoryLabels: Record<EmailCategory, string> = {
  action_required: "Action requise",
  informational: "Information",
  newsletter: "Newsletter",
  receipt: "Reçu",
  security: "Sécurité",
  spam: "Indésirable",
  other: "Autre",
};

const priorityLabels: Record<Priority, string> = {
  critical: "Critique",
  high: "Haute",
  normal: "Normale",
  low: "Basse",
};

const filterLabels: Record<ViewFilter, string> = {
  all: "Tous",
  focus: "À regarder",
  actions: "Avec actions",
  risk: "À risque",
};

function Icon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  const paths: Record<IconName, React.ReactNode> = {
    arrow: <path d="m5 12 14 0m-5-5 5 5-5 5" />,
    check: <path d="m5 12 4 4L19 6" />,
    chevron: <path d="m9 18 6-6-6-6" />,
    inbox: (
      <>
        <path d="M4 5h16l2 9v5H2v-5l2-9Z" />
        <path d="M2 14h5l2 3h6l2-3h5" />
      </>
    ),
    lock: (
      <>
        <rect x="5" y="10" width="14" height="10" rx="2" />
        <path d="M8 10V7a4 4 0 0 1 8 0v3" />
      </>
    ),
    mic: (
      <>
        <rect x="9" y="3" width="6" height="11" rx="3" />
        <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
      </>
    ),
    refresh: (
      <>
        <path d="M20 7v5h-5" />
        <path d="M19 12a7 7 0 1 0-2 5" />
      </>
    ),
    search: (
      <>
        <circle cx="11" cy="11" r="6" />
        <path d="m16 16 4 4" />
      </>
    ),
    shield: <path d="M12 3 4.5 6v5.5c0 4.8 3.2 8 7.5 9.5 4.3-1.5 7.5-4.7 7.5-9.5V6L12 3Zm-3 9 2 2 4-5" />,
    spark: <path d="m12 2 1.8 6.2L20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8L12 2Zm7 14 .7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7L19 16Z" />,
    warning: (
      <>
        <path d="M12 3 2.8 20h18.4L12 3Z" />
        <path d="M12 9v5m0 3v.1" />
      </>
    ),
  };

  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {paths[name]}
    </svg>
  );
}

function accountIdFor(analysis: EmailAnalysis) {
  return analysis.message_ref.split(":")[1] ?? "inconnu";
}

function isRisky(analysis: EmailAnalysis) {
  return (
    analysis.category === "security" ||
    analysis.safety.risk_level === "high" ||
    analysis.safety.risk_level === "medium" ||
    analysis.safety.prompt_injection_detected
  );
}

function isFocus(analysis: EmailAnalysis) {
  return (
    analysis.needs_human_review ||
    analysis.priority === "critical" ||
    analysis.priority === "high" ||
    analysis.category === "action_required" ||
    isRisky(analysis)
  );
}

function formatMoment(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "date inconnue";
  return new Intl.DateTimeFormat("fr-FR", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Paris",
  }).format(date);
}

function formatToday(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Aujourd’hui";
  const formatted = new Intl.DateTimeFormat("fr-FR", {
    weekday: "long",
    day: "numeric",
    month: "long",
    timeZone: "Europe/Paris",
  }).format(date);
  return formatted.charAt(0).toUpperCase() + formatted.slice(1);
}

function compactId(value: string) {
  return value.length > 12 ? `${value.slice(0, 6)}…${value.slice(-4)}` : value;
}

function normalizeError(payload: unknown, fallback: string) {
  if (!payload || typeof payload !== "object") return fallback;
  const candidate = payload as { detail?: unknown; message?: unknown };
  if (typeof candidate.detail === "string") return candidate.detail;
  if (typeof candidate.message === "string") return candidate.message;
  return fallback;
}

export function Dashboard({ initialSnapshot }: { initialSnapshot: DashboardSnapshot }) {
  const [snapshot, setSnapshot] = useState(initialSnapshot);
  const [selectedId, setSelectedId] = useState(initialSnapshot.analyses[0]?.analysis_id ?? null);
  const [accountFilter, setAccountFilter] = useState("all");
  const [viewFilter, setViewFilter] = useState<ViewFilter>("all");
  const [search, setSearch] = useState("");
  const [isSyncing, setIsSyncing] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [isInterpreting, setIsInterpreting] = useState(false);
  const recordingRef = useRef<VoiceRecording | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const filteredAnalyses = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("fr");
    return snapshot.analyses.filter((analysis) => {
      if (accountFilter !== "all" && accountIdFor(analysis) !== accountFilter) return false;
      if (viewFilter === "focus" && !isFocus(analysis)) return false;
      if (viewFilter === "actions" && analysis.action_items.length === 0) return false;
      if (viewFilter === "risk" && !isRisky(analysis)) return false;
      if (!query) return true;
      const searchable = [
        analysis.summary,
        categoryLabels[analysis.category],
        accountIdFor(analysis),
        ...analysis.key_points,
        ...analysis.action_items.map((item) => item.description),
      ]
        .join(" ")
        .toLocaleLowerCase("fr");
      return searchable.includes(query);
    });
  }, [accountFilter, search, snapshot.analyses, viewFilter]);

  const selected =
    filteredAnalyses.find((analysis) => analysis.analysis_id === selectedId) ??
    filteredAnalyses[0] ??
    null;
  const focusCount = snapshot.analyses.filter(isFocus).length;
  const riskCount = snapshot.analyses.filter(isRisky).length;
  const actionAnalysisCount = snapshot.analyses.filter(
    (analysis) => analysis.action_items.length > 0,
  ).length;
  const actionCount = snapshot.analyses.reduce(
    (total, analysis) => total + analysis.action_items.length,
    0,
  );

  async function refreshSnapshot() {
    const response = await fetch("/api/ezer/snapshot", { cache: "no-store" });
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(normalizeError(payload, "Le tableau n’a pas pu être actualisé."));
    }
    const nextSnapshot = payload as DashboardSnapshot;
    setSnapshot(nextSnapshot);
    setSelectedId((current) =>
      nextSnapshot.analyses.some((analysis) => analysis.analysis_id === current)
        ? current
        : (nextSnapshot.analyses[0]?.analysis_id ?? null),
    );
  }

  const knownAccountIds = useMemo(
    () => snapshot.accounts.map((account) => account.id),
    [snapshot.accounts],
  );

  const applyIntent = useCallback(
    (intent: VoiceIntent) => {
      switch (intent.action) {
        case "set_view_filter":
          if (intent.view_filter !== null) setViewFilter(intent.view_filter);
          break;
        case "set_account_filter":
          if (intent.account_id === "all" || knownAccountIds.includes(intent.account_id ?? "")) {
            setAccountFilter(intent.account_id ?? "all");
          }
          break;
        case "set_search":
          setSearch(intent.search ?? "");
          break;
        case "refresh":
          void refreshSnapshot().catch(() =>
            setError("Le tableau n’a pas pu être actualisé."),
          );
          break;
        case "sync": {
          const scope =
            intent.account_id !== null && knownAccountIds.includes(intent.account_id)
              ? intent.account_id
              : "all";
          void handleSync(scope);
          break;
        }
        default:
          break;
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [knownAccountIds],
  );

  async function handleVoice() {
    setError(null);

    // Second press: stop recording and interpret what was said.
    if (recordingRef.current !== null) {
      const recording = recordingRef.current;
      recordingRef.current = null;
      setIsListening(false);
      setIsInterpreting(true);
      try {
        const audio = await recording.stop();
        const response = await fetch("/api/ezer/voice", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ audio, account_ids: knownAccountIds }),
        });
        const payload: unknown = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error(normalizeError(payload, "La commande vocale n’a pas abouti."));
        }
        const command = payload as VoiceCommandResponse;
        if (command.intent.action === "none") {
          setNotice(command.transcript || "Commande non comprise.");
        } else {
          setNotice(command.transcript);
          applyIntent(command.intent);
        }
      } catch (reason) {
        setError(
          reason instanceof VoiceCaptureError || reason instanceof Error
            ? reason.message
            : "La commande vocale n’a pas abouti.",
        );
      } finally {
        setIsInterpreting(false);
      }
      return;
    }

    // First press: open the microphone.
    setNotice(null);
    try {
      recordingRef.current = await startRecording();
      setIsListening(true);
      setNotice("À l’écoute — appuyez de nouveau pour envoyer.");
    } catch (reason) {
      recordingRef.current = null;
      setIsListening(false);
      setError(
        reason instanceof VoiceCaptureError
          ? reason.message
          : "Le micro n’a pas pu être ouvert.",
      );
    }
  }

  async function handleSync(scope: string = accountFilter) {
    setError(null);
    setNotice(null);
    if (snapshot.mode === "demo") {
      setNotice("Aperçu local — renseignez EZER_API_URL et EZER_API_KEY pour synchroniser.");
      return;
    }

    setIsSyncing(true);
    try {
      const body = scope === "all" ? { limit: 50 } : { account_ids: [scope], limit: 50 };
      const response = await fetch("/api/ezer/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(normalizeError(payload, "La synchronisation n’a pas abouti."));
      }
      const sync = payload as SyncResponse;
      const processed = sync.reports.reduce((total, report) => total + report.processed, 0);
      const failed = sync.reports.reduce((total, report) => total + report.failed, 0);
      await refreshSnapshot();
      setNotice(
        failed > 0
          ? `${processed} message${processed > 1 ? "s" : ""} analysé${processed > 1 ? "s" : ""}, ${failed} à reprendre.`
          : `${processed} message${processed > 1 ? "s" : ""} analysé${processed > 1 ? "s" : ""}.`,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "La synchronisation n’a pas abouti.");
    } finally {
      setIsSyncing(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#main-content" aria-label="Ezer — aller au contenu">
          <span className="brand-mark" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
          <span className="brand-name">ezer</span>
          <span className="brand-tag">mail intelligence</span>
        </a>

        <div className="topbar-status">
          <span className={`live-status live-status--${snapshot.health}`}>
            <span aria-hidden="true" />
            {snapshot.health === "ready" ? "Service prêt" : "Service indisponible"}
          </span>
          <span className="privacy-note">
            <Icon name="lock" /> Lecture seule
          </span>
          <span className="avatar" aria-hidden="true">EZ</span>
        </div>
      </header>

      <div className="workspace">
        <aside className="sidebar" aria-label="Navigation Ezer">
          <div className="sidebar-section">
            <p className="sidebar-label">Vue</p>
            <nav className="view-nav" aria-label="Filtres d’analyse">
              {(Object.keys(filterLabels) as ViewFilter[]).map((filter) => {
                const count =
                  filter === "all"
                    ? snapshot.total
                    : filter === "focus"
                      ? focusCount
                      : filter === "actions"
                        ? actionAnalysisCount
                        : riskCount;
                return (
                  <button
                    type="button"
                    className={viewFilter === filter ? "is-active" : ""}
                    onClick={() => setViewFilter(filter)}
                    aria-pressed={viewFilter === filter}
                    key={filter}
                  >
                    <span>
                      <Icon
                        name={
                          filter === "all"
                            ? "inbox"
                            : filter === "focus"
                              ? "spark"
                              : filter === "actions"
                                ? "check"
                                : "shield"
                        }
                      />
                      {filterLabels[filter]}
                    </span>
                    <strong>{count}</strong>
                  </button>
                );
              })}
            </nav>
          </div>

          <div className="sidebar-section account-section">
            <p className="sidebar-label">Boîtes suivies</p>
            <button
              className={`account-row ${accountFilter === "all" ? "is-active" : ""}`}
              type="button"
              onClick={() => setAccountFilter("all")}
              aria-pressed={accountFilter === "all"}
            >
              <span className="provider-dot provider-dot--all" aria-hidden="true" />
              <span>Toutes les boîtes</span>
              <strong>{snapshot.accounts.length}</strong>
            </button>
            {snapshot.accounts.map((account) => (
              <button
                className={`account-row ${accountFilter === account.id ? "is-active" : ""}`}
                type="button"
                key={account.id}
                onClick={() => setAccountFilter(account.id)}
                aria-pressed={accountFilter === account.id}
              >
                <span className={`provider-dot provider-dot--${account.provider}`} aria-hidden="true" />
                <span>
                  <small>{account.provider === "gmail" ? "Gmail" : "Outlook"}</small>
                  {account.id}
                </span>
                <Icon name="chevron" />
              </button>
            ))}
          </div>

          <div className="read-only-card">
            <Icon name="shield" />
            <div>
              <strong>Vous gardez la main.</strong>
              <p>Ezer ne répond, ne déplace et ne supprime aucun message.</p>
            </div>
          </div>
        </aside>

        <main className="main-stage" id="main-content">
          <section className="hero" aria-labelledby="page-title">
            <div>
              <p className="eyebrow">{formatToday(snapshot.generatedAt)}</p>
              <h1 id="page-title">Votre boîte,<br /><em>décantée.</em></h1>
              <p className="hero-copy">Ezer lit les signaux. Vous gardez les décisions.</p>
            </div>
            <div className="hero-actions">
              <span className={`mode-chip mode-chip--${snapshot.mode}`}>
                {snapshot.mode === "live" ? "Données en direct" : "Mode aperçu"}
              </span>
              <button
                className={`voice-button${isListening ? " is-listening" : ""}`}
                type="button"
                onClick={() => void handleVoice()}
                disabled={isInterpreting}
                aria-pressed={isListening}
                title="Dictez une commande : « synchronise gmail », « montre les mails à risque »"
              >
                <Icon name="mic" className={isListening ? "is-pulsing" : ""} />
                {isInterpreting ? "Analyse…" : isListening ? "J’écoute…" : "Parler"}
              </button>
              <button className="sync-button" type="button" onClick={() => void handleSync()} disabled={isSyncing}>
                <Icon name="refresh" className={isSyncing ? "is-spinning" : ""} />
                {isSyncing ? "Synchronisation…" : "Synchroniser"}
              </button>
            </div>
          </section>

          {(notice || error) && (
            <div className={`toast ${error ? "toast--error" : "toast--success"}`} role="status">
              <Icon name={error ? "warning" : "check"} />
              <span>{error ?? notice}</span>
              <button
                type="button"
                onClick={() => {
                  setError(null);
                  setNotice(null);
                }}
                aria-label="Fermer la notification"
              >
                ×
              </button>
            </div>
          )}

          <section className="signal-overview" aria-label="Résumé des signaux">
            <article className="metric metric--focus">
              <p>À regarder</p>
              <strong>{focusCount.toString().padStart(2, "0")}</strong>
              <span>priorité ou revue humaine</span>
            </article>
            <article className="metric metric--action">
              <p>Actions extraites</p>
              <strong>{actionCount.toString().padStart(2, "0")}</strong>
              <span>sans modifier vos emails</span>
            </article>
            <article className="metric metric--risk">
              <p>Signaux de risque</p>
              <strong>{riskCount.toString().padStart(2, "0")}</strong>
              <span>phishing ou contenu hostile</span>
            </article>
            <div className="signal-ribbon" aria-label={`${snapshot.analyses.length} analyses récentes`}>
              <div className="signal-ribbon__label">
                <span>Flux récent</span>
                <small>{snapshot.analyses.length} signaux</small>
              </div>
              <div className="signal-ribbon__track" aria-hidden="true">
                {snapshot.analyses.slice(0, 18).map((analysis) => (
                  <span
                    key={analysis.analysis_id}
                    className={`signal-tick signal-tick--${analysis.priority} ${isRisky(analysis) ? "is-risk" : ""}`}
                  />
                ))}
              </div>
            </div>
          </section>

          <ConnectMailbox />

          <AssistantPanel />

          <section className="analysis-section" aria-labelledby="analysis-heading">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Analyse récente</p>
                <h2 id="analysis-heading">Ce qui mérite votre attention</h2>
              </div>
              <label className="search-field">
                <span className="sr-only">Rechercher dans les analyses</span>
                <Icon name="search" />
                <input
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Rechercher un signal"
                />
              </label>
            </div>

            <div className="analysis-layout">
              <div className="analysis-list" aria-label="Analyses">
                {filteredAnalyses.length === 0 ? (
                  <div className="empty-state">
                    <Icon name="inbox" />
                    <h3>Aucun signal dans cette vue</h3>
                    <p>Retirez un filtre ou synchronisez la boîte sélectionnée.</p>
                    <button
                      type="button"
                      onClick={() => {
                        setSearch("");
                        setViewFilter("all");
                        setAccountFilter("all");
                      }}
                    >
                      Réinitialiser les filtres
                    </button>
                  </div>
                ) : (
                  filteredAnalyses.map((analysis) => {
                    const active = selected?.analysis_id === analysis.analysis_id;
                    return (
                      <button
                        className={`analysis-card analysis-card--${analysis.priority} ${active ? "is-active" : ""}`}
                        type="button"
                        key={analysis.analysis_id}
                        onClick={() => setSelectedId(analysis.analysis_id)}
                        aria-pressed={active}
                      >
                        <span className="analysis-card__rail" aria-hidden="true" />
                        <span className="analysis-card__body">
                          <span className="analysis-card__meta">
                            <span className={`priority priority--${analysis.priority}`}>
                              {priorityLabels[analysis.priority]}
                            </span>
                            <span>{categoryLabels[analysis.category]}</span>
                            <time dateTime={analysis.created_at}>{formatMoment(analysis.created_at)}</time>
                          </span>
                          <strong>{analysis.summary}</strong>
                          <span className="analysis-card__footer">
                            <span>
                              <span className="provider-dot provider-dot--small" aria-hidden="true" />
                              {accountIdFor(analysis)}
                            </span>
                            <span>
                              {analysis.action_items.length > 0 && (
                                <small>{analysis.action_items.length} action{analysis.action_items.length > 1 ? "s" : ""}</small>
                              )}
                              {isRisky(analysis) && <small className="risk-pill">Risque détecté</small>}
                              {analysis.needs_human_review && <small>À vérifier</small>}
                            </span>
                          </span>
                        </span>
                        <Icon name="chevron" className="analysis-card__chevron" />
                      </button>
                    );
                  })
                )}
              </div>

              <aside className="detail-panel" aria-label="Détail de l’analyse">
                {selected ? <AnalysisDetail analysis={selected} /> : <DetailPlaceholder />}
              </aside>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}

function AnalysisDetail({ analysis }: { analysis: EmailAnalysis }) {
  const confidence = Math.round(
    (analysis.triage?.confidence ?? analysis.safety.confidence) * 100,
  );
  const phishing = Math.round(analysis.safety.phishing_likelihood * 100);

  return (
    <div className="detail-content">
      <div className="detail-topline">
        <span className={`priority priority--${analysis.priority}`}>
          {priorityLabels[analysis.priority]}
        </span>
        <span>{categoryLabels[analysis.category]}</span>
        <span className="detail-id" title={analysis.analysis_id}>{compactId(analysis.analysis_id)}</span>
      </div>

      <div className="detail-title">
        <span className="detail-title__icon"><Icon name="spark" /></span>
        <div>
          <p>Synthèse Ezer</p>
          <h3>{analysis.summary}</h3>
        </div>
      </div>

      {analysis.key_points.length > 0 && (
        <section className="detail-block">
          <h4>À retenir</h4>
          <ul className="key-points">
            {analysis.key_points.map((point, index) => (
              <li key={`${point}-${index}`}>
                <span aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
                {point}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="detail-block">
        <div className="detail-block__heading">
          <h4>Actions explicites</h4>
          <span>{analysis.action_items.length}</span>
        </div>
        {analysis.action_items.length > 0 ? (
          <ul className="action-list">
            {analysis.action_items.map((item, index) => (
              <li key={`${item.description}-${index}`}>
                <span className="action-check" aria-hidden="true"><Icon name="check" /></span>
                <div>
                  <strong>{item.description}</strong>
                  <p>
                    {item.owner ? `Pour ${item.owner}` : "Responsable non indiqué"}
                    {item.due_date ? ` · Échéance ${item.due_date}` : ""}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="quiet-empty"><Icon name="check" /> Aucune action explicite dans ce message.</p>
        )}
      </section>

      <section className={`safety-card safety-card--${analysis.safety.risk_level}`}>
        <div className="safety-card__icon"><Icon name="shield" /></div>
        <div className="safety-card__copy">
          <p>Contrôle de sécurité</p>
          <strong>
            {analysis.safety.risk_level === "none"
              ? "Aucun signal hostile"
              : `Risque ${analysis.safety.risk_level === "high" ? "élevé" : analysis.safety.risk_level === "medium" ? "modéré" : "faible"}`}
          </strong>
          <span>{analysis.safety.rationale}</span>
        </div>
        <div className="safety-gauges">
          <span><small>Confiance</small><strong>{confidence}%</strong></span>
          <span><small>Phishing</small><strong>{phishing}%</strong></span>
        </div>
      </section>

      <div className="detail-footer">
        <span><Icon name="lock" /> Analyse en lecture seule</span>
        <time dateTime={analysis.created_at}>{formatMoment(analysis.created_at)}</time>
      </div>
    </div>
  );
}

function DetailPlaceholder() {
  return (
    <div className="detail-placeholder">
      <span><Icon name="spark" /></span>
      <h3>Sélectionnez un signal</h3>
      <p>Sa synthèse, ses actions et son contrôle de sécurité apparaîtront ici.</p>
    </div>
  );
}

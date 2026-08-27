"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Panneau de connexion d'une boîte mail.
 *
 * Tout le travail sensible reste côté backend : ce composant demande le démarrage du flux, affiche
 * l'adresse Microsoft et le code à saisir, puis interroge l'état jusqu'à ce qu'il soit résolu.
 */

type ConnectionStatus = "disconnected" | "pending" | "connected" | "failed";

interface MailboxConnection {
  account_id: string;
  provider: "gmail" | "outlook";
  mailbox: string | null;
  status: ConnectionStatus;
  code: string | null;
  verification_uri: string | null;
  user_code: string | null;
  connected_at: string | null;
}

const POLL_INTERVAL_MS = 3_000;

const statusLabels: Record<ConnectionStatus, string> = {
  disconnected: "Non connectée",
  pending: "En attente de votre validation",
  connected: "Connectée",
  failed: "Échec",
};

/** Les codes du backend sont volontairement stables : ils sont traduits ici, pas là-bas. */
const codeMessages: Record<string, string> = {
  account_not_connected: "Cette boîte n’est pas encore connectée.",
  authorization_declined: "La demande a été refusée dans la fenêtre Microsoft.",
  client_not_configured:
    "Aucune application Microsoft n’est configurée côté backend (EZER_OUTLOOK_CLIENT_ID).",
  device_code_expired: "Le code a expiré. Relancez la connexion.",
  device_flow_failed: "Microsoft a refusé la demande de connexion.",
  expired_token: "Le code a expiré. Relancez la connexion.",
  mailbox_lookup_failed: "La boîte connectée n’a pas pu être vérifiée.",
  mailbox_mismatch: "Vous vous êtes connecté avec une autre adresse que celle configurée.",
  mailbox_not_configured: "Aucune adresse n’est déclarée pour ce compte côté backend.",
  microsoft_unavailable: "Microsoft est momentanément indisponible.",
  microsoft_unreachable: "Microsoft est injoignable depuis le backend.",
  offline_access_denied: "L’autorisation d’accès prolongé a été refusée.",
  provider_not_supported: "Ce fournisseur ne gère pas ce mode de connexion.",
  public_client_flow_not_enabled:
    "L’application Microsoft doit autoriser les flux client public.",
  reauthentication_required: "L’autorisation a expiré. Reconnectez la boîte.",
  token_refresh_failed: "Le renouvellement de l’autorisation a échoué.",
};

function describe(connection: MailboxConnection): string | null {
  if (connection.code === null) return null;
  return codeMessages[connection.code] ?? "La connexion n’a pas abouti.";
}

async function readError(response: Response, fallback: string): Promise<string> {
  const payload: unknown = await response.json().catch(() => null);
  if (payload !== null && typeof payload === "object" && "message" in payload) {
    const message = (payload as { message?: unknown }).message;
    if (typeof message === "string" && message.length > 0) return message;
  }
  return fallback;
}

export function ConnectMailbox() {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [mailboxes, setMailboxes] = useState<MailboxConnection[]>([]);
  const [busyAccountId, setBusyAccountId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const replace = useCallback((connection: MailboxConnection) => {
    setMailboxes((current) =>
      current.map((mailbox) =>
        mailbox.account_id === connection.account_id
          ? { ...connection, mailbox: connection.mailbox ?? mailbox.mailbox }
          : mailbox,
      ),
    );
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await fetch("/api/ezer/connect", { cache: "no-store" });
        if (!response.ok) {
          if (!cancelled) setConfigured(false);
          return;
        }
        const payload = (await response.json()) as {
          configured: boolean;
          mailboxes: MailboxConnection[];
        };
        if (cancelled) return;
        setConfigured(payload.configured);
        setMailboxes(payload.mailboxes);
      } catch {
        if (!cancelled) setConfigured(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Un flux en attente est suivi tant qu'il dure : Microsoft ne rappelle pas ce navigateur.
  const pendingAccountId =
    mailboxes.find((mailbox) => mailbox.status === "pending")?.account_id ?? null;

  useEffect(() => {
    if (pendingAccountId === null) return;
    let cancelled = false;
    const timer = setInterval(() => {
      void (async () => {
        try {
          const response = await fetch(
            `/api/ezer/connect?account_id=${encodeURIComponent(pendingAccountId)}`,
            { cache: "no-store" },
          );
          if (!response.ok || cancelled) return;
          replace((await response.json()) as MailboxConnection);
        } catch {
          // Une interrogation manquée est sans conséquence : la suivante réessaie.
        }
      })();
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [pendingAccountId, replace]);

  async function act(accountId: string, method: "POST" | "DELETE") {
    setError(null);
    setBusyAccountId(accountId);
    try {
      const response = await fetch(
        `/api/ezer/connect?account_id=${encodeURIComponent(accountId)}`,
        { method },
      );
      if (!response.ok) {
        throw new Error(await readError(response, "La connexion n’a pas pu être lancée."));
      }
      replace((await response.json()) as MailboxConnection);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "La connexion n’a pas abouti.");
    } finally {
      setBusyAccountId(null);
    }
  }

  if (configured === null) return null;

  return (
    <section className="connect-panel" aria-labelledby="connect-heading">
      <style>{PANEL_STYLES}</style>
      <div className="connect-panel__head">
        <p className="connect-panel__eyebrow">Boîtes mail</p>
        <h2 className="connect-panel__title" id="connect-heading">
          Connecter une boîte
        </h2>
      </div>

      {!configured ? (
        <p className="connect-panel__empty">
          Le tableau de bord tourne en démonstration. Renseignez <code>EZER_API_URL</code> et{" "}
          <code>EZER_API_KEY</code> pour connecter une vraie boîte.
        </p>
      ) : mailboxes.length === 0 ? (
        <p className="connect-panel__empty">
          Aucune boîte connectable n’est déclarée côté backend. Ajoutez son adresse dans{" "}
          <code>EZER_ACCOUNTS_JSON</code>.
        </p>
      ) : (
        <ul className="connect-panel__list">
          {mailboxes.map((mailbox) => {
            const detail = describe(mailbox);
            const busy = busyAccountId === mailbox.account_id;
            return (
              <li className="connect-card" key={mailbox.account_id}>
                <div className="connect-card__identity">
                  <span className="connect-card__mailbox">{mailbox.mailbox ?? mailbox.account_id}</span>
                  <span className={`connect-card__status connect-card__status--${mailbox.status}`}>
                    {statusLabels[mailbox.status]}
                  </span>
                </div>

                {mailbox.status === "pending" &&
                mailbox.verification_uri !== null &&
                mailbox.user_code !== null ? (
                  <p className="connect-card__challenge">
                    Ouvrez{" "}
                    <a href={mailbox.verification_uri} rel="noreferrer noopener" target="_blank">
                      {mailbox.verification_uri}
                    </a>{" "}
                    puis saisissez le code <code>{mailbox.user_code}</code>. Connectez-vous avec{" "}
                    <strong>{mailbox.mailbox}</strong>.
                  </p>
                ) : null}

                {detail !== null ? <p className="connect-card__detail">{detail}</p> : null}

                <div className="connect-card__actions">
                  {mailbox.status === "connected" ? (
                    <button
                      className="connect-card__button"
                      disabled={busy}
                      onClick={() => void act(mailbox.account_id, "DELETE")}
                      type="button"
                    >
                      Déconnecter
                    </button>
                  ) : (
                    <button
                      className="connect-card__button connect-card__button--primary"
                      disabled={busy || mailbox.status === "pending"}
                      onClick={() => void act(mailbox.account_id, "POST")}
                      type="button"
                    >
                      {mailbox.status === "pending" ? "En attente…" : "Connecter"}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {error !== null ? (
        <p className="connect-panel__error" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}

const PANEL_STYLES = `
.connect-panel {
  display: grid;
  gap: 0.9rem;
  padding: 1.25rem 1.4rem;
  border: 1px solid color-mix(in srgb, currentColor 12%, transparent);
  border-radius: 18px;
  background: color-mix(in srgb, currentColor 3%, transparent);
}
.connect-panel__eyebrow {
  margin: 0;
  font-size: 0.72rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  opacity: 0.6;
}
.connect-panel__title { margin: 0.15rem 0 0; font-size: 1.15rem; }
.connect-panel__empty, .connect-panel__error { margin: 0; font-size: 0.9rem; opacity: 0.75; }
.connect-panel__error { color: #b4232b; opacity: 1; }
.connect-panel__list { display: grid; gap: 0.75rem; margin: 0; padding: 0; list-style: none; }
.connect-card {
  display: grid;
  gap: 0.55rem;
  padding: 0.9rem 1rem;
  border: 1px solid color-mix(in srgb, currentColor 12%, transparent);
  border-radius: 14px;
}
.connect-card__identity {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.connect-card__mailbox { font-weight: 600; }
.connect-card__status {
  font-size: 0.75rem;
  padding: 0.15rem 0.6rem;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, currentColor 20%, transparent);
}
.connect-card__status--connected { color: #17795e; border-color: #17795e; }
.connect-card__status--pending { color: #9a6400; border-color: #9a6400; }
.connect-card__status--failed { color: #b4232b; border-color: #b4232b; }
.connect-card__challenge, .connect-card__detail { margin: 0; font-size: 0.9rem; line-height: 1.5; }
.connect-card__challenge code {
  font-size: 1rem;
  letter-spacing: 0.12em;
  padding: 0.1rem 0.4rem;
  border-radius: 6px;
  background: color-mix(in srgb, currentColor 10%, transparent);
}
.connect-card__actions { display: flex; gap: 0.5rem; }
.connect-card__button {
  font: inherit;
  font-size: 0.88rem;
  padding: 0.45rem 1rem;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, currentColor 25%, transparent);
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.connect-card__button--primary { background: color-mix(in srgb, currentColor 12%, transparent); }
.connect-card__button[disabled] { cursor: not-allowed; opacity: 0.55; }
`;

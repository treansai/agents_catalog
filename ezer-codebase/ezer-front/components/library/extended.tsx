"use client";

import { EzCard, EzPill } from "./primitives";

export function FlightStatus() {
  return (
    <EzCard style={{ width: 300, padding: 20, display: "grid", gap: 14 }}>
      <span className="ezc-mono" style={{ fontSize: 10, letterSpacing: "0.14em", color: "#6b5a5c", textTransform: "uppercase" }}>
        statut de vol
      </span>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span style={{ fontSize: 17, fontWeight: 600 }}>✈ OS 87</span>
        <span className="ezc-mono" style={{ fontSize: 10, color: "#4d3f41" }}>
          lun. 15 déc.
        </span>
      </div>
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <span style={{ fontSize: 15, color: "#d8cfd0" }}>Vienne</span>
        <span style={{ color: "#880d1e" }}>→</span>
        <span style={{ fontSize: 15, color: "#d8cfd0" }}>New York</span>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", paddingTop: 12, borderTop: "1px solid #140f10" }}>
        <span style={{ display: "grid", gap: 2 }}>
          <span className="ezc-mono" style={{ fontSize: 9.5, color: "#4d3f41" }}>
            DÉPART
          </span>
          <span style={{ fontSize: 14, fontWeight: 600 }}>10:15</span>
        </span>
        <span style={{ display: "grid", gap: 2, justifyItems: "center" }}>
          <span className="ezc-mono" style={{ fontSize: 9.5, color: "#4d3f41" }}>
            STATUT
          </span>
          <span style={{ fontSize: 12, color: "#7da57d" }}>à l&apos;heure</span>
        </span>
        <span style={{ display: "grid", gap: 2, justifyItems: "end" }}>
          <span className="ezc-mono" style={{ fontSize: 9.5, color: "#4d3f41" }}>
            ARRIVÉE
          </span>
          <span style={{ fontSize: 14, fontWeight: 600 }}>14:30</span>
        </span>
      </div>
    </EzCard>
  );
}

export function TaskCard() {
  return (
    <EzCard style={{ width: 280, padding: 20, display: "grid", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span style={{ fontSize: 15, fontWeight: 600 }}>Relire la pull request</span>
        <span className="ezc-mono" style={{ fontSize: 11, color: "#ff5a6e" }}>
          !
        </span>
      </div>
      <span style={{ fontSize: 12.5, lineHeight: 1.55, color: "#8a7679" }}>
        Relire et approuver les changements du module d&apos;authentification.
      </span>
      <div style={{ display: "flex", gap: 8 }}>
        <span className="ezc-tag is-urgent">aujourd&apos;hui</span>
        <span className="ezc-tag">backend</span>
      </div>
    </EzCard>
  );
}

export function OrderCard() {
  return (
    <EzCard style={{ width: 280, padding: 20, display: "grid", gap: 10 }}>
      <span style={{ fontSize: 15, fontWeight: 600 }}>☕ Sunrise Coffee</span>
      <div style={{ display: "grid", gap: 6, padding: "8px 0", borderBottom: "1px solid #140f10" }}>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ fontSize: 12.5, color: "#d8cfd0" }}>Oat Milk Latte</span>
          <span className="ezc-mono" style={{ fontSize: 11, color: "#a89a9c" }}>
            6,45 €
          </span>
        </div>
        <span className="ezc-mono" style={{ fontSize: 9.5, color: "#4d3f41" }}>
          grande · extra shot
        </span>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span style={{ fontSize: 12.5, color: "#d8cfd0" }}>Croissant chocolat</span>
          <span className="ezc-mono" style={{ fontSize: 11, color: "#a89a9c" }}>
            4,25 €
          </span>
        </div>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>Total</span>
        <span style={{ fontSize: 16, fontWeight: 600, color: "#d97883" }}>11,66 €</span>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <EzPill variant="primary">commander</EzPill>
        <EzPill>ajouter au panier</EzPill>
      </div>
    </EzCard>
  );
}

export function PlaylistCard() {
  const tracks = [
    { n: 1, title: "Weightless", artist: "Marconi Union", duration: "8:09" },
    { n: 2, title: "Clair de Lune", artist: "Debussy", duration: "5:12" },
    { n: 3, title: "Ambient Light", artist: "Brian Eno", duration: "6:45" },
  ];
  return (
    <EzCard style={{ width: 280, padding: 20, display: "grid", gap: 6 }}>
      <span style={{ fontSize: 15, fontWeight: 600 }}>♫ Focus Flow</span>
      {tracks.map((track) => (
        <div
          key={track.n}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "10px 0",
            borderBottom: track.n === 3 ? 0 : "1px solid #140f10",
          }}
        >
          <span className="ezc-mono" style={{ fontSize: 10, color: "#4d3f41" }}>
            {track.n}
          </span>
          <span
            style={{
              width: 34,
              height: 34,
              borderRadius: 8,
              background: "repeating-linear-gradient(45deg,#1a1314,#1a1314 4px,#140f10 4px,#140f10 8px)",
              flexShrink: 0,
            }}
          />
          <span style={{ display: "grid", gap: 1, flex: 1, minWidth: 0 }}>
            <span style={{ fontSize: 12.5, color: "#d8cfd0" }}>{track.title}</span>
            <span className="ezc-mono" style={{ fontSize: 9.5, color: "#5c4d4f" }}>
              {track.artist}
            </span>
          </span>
          <span className="ezc-mono" style={{ fontSize: 10, color: "#4d3f41" }}>
            {track.duration}
          </span>
        </div>
      ))}
    </EzCard>
  );
}

export function RecipeCard() {
  return (
    <EzCard style={{ width: 280 }}>
      <div
        style={{
          height: 120,
          background: "repeating-linear-gradient(45deg,#1a1314,#1a1314 6px,#140f10 6px,#140f10 12px)",
          display: "grid",
          placeItems: "center",
        }}
      >
        <span className="ezc-mono" style={{ fontSize: 9.5, letterSpacing: "0.1em", color: "#4d3f41" }}>
          photo du plat
        </span>
      </div>
      <div style={{ padding: 18, display: "grid", gap: 8 }}>
        <span style={{ fontSize: 15, fontWeight: 600 }}>Bol de quinoa méditerranéen</span>
        <span className="ezc-mono" style={{ fontSize: 10, color: "#d97883" }}>
          ★ 4,9 · 1 247 avis
        </span>
        <div style={{ display: "flex", gap: 14 }}>
          <span className="ezc-mono" style={{ fontSize: 10, color: "#6b5a5c" }}>
            15 min prépa
          </span>
          <span className="ezc-mono" style={{ fontSize: 10, color: "#6b5a5c" }}>
            20 min cuisson
          </span>
          <span className="ezc-mono" style={{ fontSize: 10, color: "#6b5a5c" }}>
            4 pers.
          </span>
        </div>
      </div>
    </EzCard>
  );
}

export function ProfileCard() {
  return (
    <EzCard style={{ width: 260, padding: "24px 20px", display: "grid", justifyItems: "center", gap: 10 }}>
      <span
        style={{
          width: 72,
          height: 72,
          borderRadius: "50%",
          background: "rgba(136,13,30,0.3)",
          border: "1px solid rgba(136,13,30,0.6)",
          display: "grid",
          placeItems: "center",
          fontSize: 22,
          fontWeight: 600,
          color: "#e8b7bd",
        }}
      >
        SC
      </span>
      <span style={{ fontSize: 15, fontWeight: 600 }}>Sarah Chen</span>
      <span className="ezc-mono" style={{ fontSize: 10, color: "#6b5a5c" }}>
        @sarahchen
      </span>
      <span style={{ fontSize: 12, textAlign: "center", lineHeight: 1.5, color: "#8a7679" }}>
        Product designer chez Tech Co.
      </span>
      <div style={{ display: "flex", gap: 18, padding: "8px 0" }}>
        {[
          ["12,4K", "ABONNÉS"],
          ["892", "SUIVIS"],
          ["347", "POSTS"],
        ].map(([value, label]) => (
          <span key={label} style={{ display: "grid", justifyItems: "center" }}>
            <span style={{ fontSize: 14, fontWeight: 600 }}>{value}</span>
            <span className="ezc-mono" style={{ fontSize: 9, color: "#4d3f41" }}>
              {label}
            </span>
          </span>
        ))}
      </div>
      <EzPill variant="primary">suivre</EzPill>
    </EzCard>
  );
}

export function PlayerCard() {
  return (
    <EzCard style={{ width: 260 }}>
      <div
        style={{
          height: 130,
          background: "repeating-linear-gradient(45deg,#1a1314,#1a1314 6px,#140f10 6px,#140f10 12px)",
          display: "grid",
          placeItems: "center",
        }}
      >
        <span className="ezc-mono" style={{ fontSize: 9.5, letterSpacing: "0.1em", color: "#4d3f41" }}>
          photo du joueur
        </span>
      </div>
      <div style={{ padding: 18, display: "grid", gap: 8, justifyItems: "center" }}>
        <span style={{ fontSize: 15, fontWeight: 600 }}>Marcus Johnson</span>
        <span className="ezc-mono" style={{ fontSize: 10, color: "#d97883" }}>
          #23 · LA Lakers
        </span>
        <div style={{ display: "flex", gap: 18, paddingTop: 6 }}>
          {[
            ["28,4", "PPG"],
            ["7,2", "RPG"],
            ["6,8", "APG"],
          ].map(([value, label]) => (
            <span key={label} style={{ display: "grid", justifyItems: "center" }}>
              <span style={{ fontSize: 14, fontWeight: 600 }}>{value}</span>
              <span className="ezc-mono" style={{ fontSize: 9, color: "#4d3f41" }}>
                {label}
              </span>
            </span>
          ))}
        </div>
      </div>
    </EzCard>
  );
}

export function PurchaseForm() {
  return (
    <EzCard style={{ width: 300, padding: 20, display: "grid", gap: 12 }}>
      <span style={{ fontSize: 15, fontWeight: 600 }}>Licence Design Suite Pro</span>
      <div style={{ display: "flex", justifyContent: "space-between", padding: "10px 0", borderTop: "1px solid #140f10" }}>
        <span style={{ fontSize: 12.5, color: "#8a7679" }}>Nombre de sièges</span>
        <span style={{ fontSize: 13, fontWeight: 600, color: "#d8cfd0" }}>10 sièges</span>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", paddingBottom: 10, borderBottom: "1px solid #140f10" }}>
        <span style={{ fontSize: 12.5, color: "#8a7679" }}>Facturation</span>
        <span style={{ fontSize: 13, fontWeight: 600, color: "#d8cfd0" }}>annuelle</span>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>Total</span>
        <span style={{ fontSize: 16, fontWeight: 600, color: "#d97883" }}>1 188 €/an</span>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <EzPill variant="primary">confirmer l&apos;achat</EzPill>
        <EzPill>annuler</EzPill>
      </div>
    </EzCard>
  );
}

export function DirectoryContact() {
  return (
    <EzCard style={{ width: 260, padding: "24px 20px", display: "grid", justifyItems: "center", gap: 10 }}>
      <span
        style={{
          width: 64,
          height: 64,
          borderRadius: "50%",
          background: "rgba(136,13,30,0.3)",
          border: "1px solid rgba(136,13,30,0.6)",
          display: "grid",
          placeItems: "center",
          fontWeight: 600,
          color: "#e8b7bd",
        }}
      >
        DP
      </span>
      <span style={{ fontSize: 15, fontWeight: 600 }}>David Park</span>
      <span className="ezc-mono" style={{ fontSize: 10, color: "#6b5a5c" }}>
        engineering manager
      </span>
      <span className="ezc-mono" style={{ fontSize: 10, color: "#8a7679" }}>
        +1 (555) 234-5678
      </span>
      <span className="ezc-mono" style={{ fontSize: 10, color: "#8a7679" }}>
        david.park@company.com
      </span>
      <div style={{ display: "flex", gap: 8 }}>
        <EzPill variant="primary">appeler</EzPill>
        <EzPill>message</EzPill>
      </div>
    </EzCard>
  );
}

export function LoginForm() {
  return (
    <EzCard style={{ width: 280, padding: 20, display: "grid", gap: 12 }}>
      <span style={{ fontSize: 15, fontWeight: 600 }}>Connexion</span>
      <span className="ezc-mono" style={{ fontSize: 9.5, color: "#4d3f41" }}>
        EMAIL
      </span>
      <span style={{ fontSize: 13, color: "#8a7679", padding: "8px 12px", border: "1px solid #1c1516", borderRadius: 10 }}>
        vous@exemple.com
      </span>
      <span className="ezc-mono" style={{ fontSize: 9.5, color: "#4d3f41" }}>
        MOT DE PASSE
      </span>
      <span style={{ fontSize: 13, color: "#8a7679", padding: "8px 12px", border: "1px solid #1c1516", borderRadius: 10 }}>
        ••••••••
      </span>
      <EzPill variant="primary">se connecter</EzPill>
    </EzCard>
  );
}

export function DayAgenda() {
  return (
    <EzCard style={{ width: 260, padding: 20, display: "grid", gap: 12 }}>
      <div>
        <span style={{ fontSize: 18, fontWeight: 600 }}>Jeudi 28</span>
        <span className="ezc-mono" style={{ display: "block", fontSize: 10, color: "#6b5a5c" }}>
          août 2026
        </span>
      </div>
      {[
        ["Réunion projet Horizon", "10:00 – 10:45"],
        ["Point équipe", "15:30 – 16:00"],
        ["Sport", "19:00"],
      ].map(([title, when]) => (
        <div key={title} style={{ paddingTop: 10, borderTop: "1px solid #140f10", display: "grid", gap: 4 }}>
          <span style={{ fontSize: 13, color: "#d8cfd0" }}>{title}</span>
          <span className="ezc-mono" style={{ fontSize: 10, color: "#6b5a5c" }}>
            {when}
          </span>
        </div>
      ))}
    </EzCard>
  );
}

"use client";

import type { ReactNode } from "react";

import { ActionFeed } from "@/components/library/feedback";
import { CalendarEvent, ChatBubbles, ContactCard, ContextMenu, DraftReply, FilterChips, MailboxList, ModeToggle, SearchField, SettingsPanel, VoiceStatus } from "@/components/library/chrome";
import { ConfirmationDialog, EmptyStateView, ProgressBar, SkeletonView, StatusBanner } from "@/components/library/feedback";
import {
  DayAgenda,
  DirectoryContact,
  FlightStatus,
  LoginForm,
  OrderCard,
  PlayerCard,
  PlaylistCard,
  ProfileCard,
  PurchaseForm,
  RecipeCard,
  TaskCard,
} from "@/components/library/extended";
import { MailDetailView, MailListView } from "@/components/library/mail";
import { MetricCard, MorningBrief } from "@/components/library/metrics";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section style={{ display: "grid", gap: 16 }}>
      <h2
        className="ezc-mono"
        style={{
          fontSize: 10,
          letterSpacing: "0.18em",
          color: "#6b5a5c",
          textTransform: "uppercase",
          borderBottom: "1px solid #1a1314",
          paddingBottom: 10,
          margin: 0,
        }}
      >
        {title}
      </h2>
      <div style={{ display: "flex", gap: 24, flexWrap: "wrap", alignItems: "flex-start" }}>{children}</div>
    </section>
  );
}

export default function ComposantsPage() {
  return (
    <div className="ezc" style={{ minHeight: "100vh", background: "#050505", padding: 56, display: "grid", gap: 48 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#880d1e" }} />
        <span style={{ fontSize: 16, fontWeight: 600, color: "#f2eded" }}>ezer</span>
        <span className="ezc-mono" style={{ fontSize: 10, letterSpacing: "0.16em", color: "#4d3f41", textTransform: "uppercase" }}>
          composants
        </span>
      </div>

      <Section title="mails">
        <MailListView
          items={[
            {
              id: "1",
              sender: "Marie Lefèvre",
              subject: "Relance devis — projet Horizon",
              snippet: "Bonjour, auriez-vous eu le temps de regarder…",
              receivedAt: "2026-08-27T07:41:00.000Z",
              unread: true,
              tag: "urgent",
            },
            {
              id: "2",
              sender: "EDF",
              subject: "Votre facture d'août est disponible",
              snippet: "",
              receivedAt: "2026-08-27T06:12:00.000Z",
              unread: false,
              tag: "finances",
            },
          ]}
        />
        <MailDetailView
          subject="Relance devis — projet Horizon"
          sender="Marie Lefèvre"
          senderAddress="marie.lefevre@horizon.fr"
          receivedAt="2026-08-27T07:41:00.000Z"
          body="Bonjour, auriez-vous eu le temps de regarder le devis envoyé la semaine dernière ? Nous aimerions valider avant vendredi."
        />
      </Section>

      <Section title="actions de l'assistant">
        <ActionFeed
          items={[
            { text: "Lecture de 14 nouveaux mails", meta: "en cours…", live: true },
            { text: "Facture EDF — août 2026", meta: "classé → finances" },
            { text: "Réponse à Marie Lefèvre", meta: "en attente de votre accord" },
          ]}
        />
        <ChatBubbles
          user="« Trie ma boîte et réponds à Marie »"
          assistant="14 mails triés. J'ai préparé une réponse pour Marie — je l'envoie ?"
        />
      </Section>

      <Section title="contrôles & états">
        <ModeToggle value="opaque" />
        <VoiceStatus label="à l'écoute" />
        <VoiceStatus label="ezer répond" tone="live" />
        <ProgressBar current={9} label="tri en cours" total={14} />
      </Section>

      <Section title="saisie & recherche">
        <div style={{ width: 340, display: "grid", gap: 12 }}>
          <SearchField placeholder="rechercher un mail, un contact…" />
          <SearchField liveQuery="montre-moi les factures de juillet" placeholder="" />
          <FilterChips
            options={[
              { id: "all", label: "tous", selected: true },
              { id: "unread", label: "non lus" },
              { id: "attach", label: "pièces jointes" },
            ]}
          />
        </div>
      </Section>

      <Section title="brouillon proposé par ezer">
        <DraftReply
          body="Bonjour Marie, merci pour votre relance. Le devis est validé de notre côté — je vous renvoie la version signée avant jeudi. Bien à vous."
          title="brouillon · réponse à marie"
          tone="professionnel"
        />
      </Section>

      <Section title="superpositions">
        <ConfirmationDialog
          body="ezer enverra le brouillon tel quel. Cette action est annulable pendant 30 secondes."
          onCancel={() => undefined}
          onConfirm={() => undefined}
          reversible
          title="Envoyer la réponse à Marie ?"
        />
        <ContextMenu
          items={[
            { label: "répondre par ezer", active: true },
            { label: "classer dans…" },
            { label: "marquer urgent" },
            { label: "archiver", danger: true },
          ]}
        />
      </Section>

      <Section title="résumés & widgets">
        <MorningBrief
          body="14 nouveaux mails, dont 3 urgents. 1 réunion à 10h. J'ai archivé 6 promotions et préparé 2 réponses."
          title="Votre matinée"
          when="mer. 27 août"
        />
        <CalendarEvent day="28" source="ajouté depuis un mail" title="Réunion projet Horizon" weekday="JEU" when="10:00 – 10:45 · visio" />
        <MetricCard label="mails triés aujourd'hui" value="42" />
        <MetricCard hint="temps gagné" label="temps gagné" value="1h20" />
        <ContactCard meta="horizon.fr · 12 échanges" name="Marie Lefèvre" />
      </Section>

      <Section title="états système">
        <EmptyStateView detail="rien à trier pour l'instant" title="Boîte à zéro" />
        <SkeletonView />
        <StatusBanner
          actionLabel="reconnecter"
          detail="Le tri est en pause. Vos mails ne sont pas modifiés."
          title="Connexion boîte mail perdue"
          tone="danger"
        />
      </Section>

      <Section title="réglages">
        <SettingsPanel />
        <MailboxList
          items={[
            { address: "pro@traidano.com", status: "sync" },
            { address: "perso@gmail.com", status: "paused" },
          ]}
        />
      </Section>

      <Section title="bibliothèque étendue">
        <FlightStatus />
        <TaskCard />
        <OrderCard />
        <PlaylistCard />
        <RecipeCard />
        <ProfileCard />
        <PlayerCard />
        <PurchaseForm />
        <DirectoryContact />
        <LoginForm />
        <DayAgenda />
      </Section>
    </div>
  );
}

# IMPLEMENTED_STATE_01 — Agent UI Runtime + bibliothèque proto/v1

Date : 27 août 2026  
Périmètre : `ezer-front`, `ezer-backend`  
Transport chat : HTTP POST existant (`/api/ezer/assistant`) — pas de SSE ajouté  
Auth : BFF + clé API backend ; workspace = `account_id` de la boîte connectée

Ce document décrit ce qui a été réellement livré. Ce n’est pas un plan.

---

## Intention

Ajouter une couche **Agent UI Runtime** isolée : un agent IA choisit un composant dans une liste blanche, demande le chargement de ses données, l’affiche dans la conversation et reçoit des interactions déclaratives.

L’agent ne peut jamais envoyer :

- du JSX, du JavaScript, un chemin d’import, une URL arbitraire ;
- une fonction, un nom d’outil non enregistré, du SQL ;
- un gestionnaire d’événement sous forme de code.

Il ne produit qu’une description JSON validée (`componentId` + version + props + source de données + `fallbackText`).

L’architecture de chat existante n’a pas été remplacée. Les `views` du bot sont converties en messages `ui.render`.

---

## Décisions d’architecture

- Pas de Zod : le projet n’en avait pas. Validateur JSON Schema minimal (frontend + backend), identique dans l’esprit.
- Pas de nouvelle librairie UI ni de nouveau test runner frontend (Node test + `--experimental-strip-types`).
- Chargement React via une **table statique** `load: () => import("@/components/library/...")`. Jamais `import(cheminAgent)`.
- `userId`, `workspaceId` et permissions viennent **uniquement** de la session / boîte connectée. Ils sont retirés de tout input modèle.
- Les widgets de la « bibliothèque étendue » proto (vol, recette, playlist, login, etc.) sont visuels uniquement. Ils ne sont **pas** dans le catalogue agent.
- Mapping métier Ezer (pas de factures/CA/projets natifs) :
  - factures → mails/reçus (`invoices.search`) ;
  - CA du mois → volume de reçus (`metrics.receipts`), pas un montant extrait ;
  - suppression de projet → mise à la corbeille d’un message, avec confirmation.

---

## Protocole

`protocolVersion: "1.0"`

Messages discriminés :

| `kind`       | Rôle        | Rôle |
|--------------|-------------|------|
| `text`       | user / assistant / system | Markdown borné |
| `ui.render`  | assistant   | Affiche une instance de composant |
| `ui.patch`   | assistant   | Met à jour les props d’une instance |
| `ui.remove`  | assistant   | Retire une instance |

Un `ui.render` contient obligatoirement : `instanceId`, `componentId` (liste blanche), `componentVersion`, `props`, `fallbackText`, et une source de données optionnelle :

- `data.mode = "inline"` + `value`
- `data.mode = "resolver"` + `resolverId` + `input`

Actions utilisateur :

```ts
kind: "ui.action"
eventId, messageId, instanceId, componentId, componentVersion,
actionId, values, idempotencyKey, createdAt
```

---

## Catalogue agent (liste blanche)

Ne figurent ici que des composants réellement branchés au runtime.

| id               | Rôle                         | Résolveurs                         | Actions |
|------------------|------------------------------|------------------------------------|---------|
| `mail.list`      | Tableau paginé mails/reçus   | `invoices.search`, `messages.search`, `analyses.triage` | `messages.open`, `invoices.open`, `table.page`, `messages.trash` |
| `mail.detail`    | Message ouvert               | `messages.get`                     | `messages.trash`, `draft.reply` |
| `metric.card`    | Carte métrique               | `mailbox.stats`, `metrics.receipts` | — |
| `senders.list`   | Expéditeurs                  | `senders.tally`                    | `messages.open` |
| `confirm.dialog` | Confirmation avant mutation  | —                                  | `confirmation.confirm`, `confirmation.cancel` |
| `empty.state`    | État vide                    | —                                  | — |
| `action.feed`    | Journal d’actions            | —                                  | — |
| `morning.brief`  | Digest du matin              | `mailbox.stats`                    | — |
| `choices.chips`  | Filtres cliquables           | —                                  | `table.filter` |

Galerie visuelle (hors agent) : `/composants`.

---

## Résolveurs serveur

Implémentés dans `ezer-backend/src/agent-ui/resolvers.ts`.

| id                 | Donnée                                      |
|--------------------|---------------------------------------------|
| `messages.search`  | Messages paginés (Graph, sinon analyses)    |
| `invoices.search`  | Reçus / factures paginés                    |
| `messages.get`     | Détail d’un message                         |
| `mailbox.stats`    | Non-lus / volume de la boîte                |
| `metrics.receipts` | Nombre de reçus du mois                     |
| `senders.tally`    | Classement d’expéditeurs                    |
| `analyses.triage`  | Analyses persistées, scopées au workspace   |

Contraintes appliquées :

- timeout 12 s, cache workspace-scopé, déduplication des requêtes en vol ;
- schémas d’entrée/sortie, permissions `mail.read` ;
- pagination / tri / filtre côté serveur pour les listes ;
- pas d’URL fournie par l’agent (pas de SSRF) ;
- repli analyses JSON si Graph n’est pas connecté (`ConnectionError` ou mode demo).

---

## Actions serveur

Implémentées dans `ezer-backend/src/agent-ui/actions.ts`.

| id                     | Comportement |
|------------------------|--------------|
| `messages.open`        | Clic déclaratif sur une ligne |
| `invoices.open`        | Idem, ciblé reçus |
| `table.page`           | Pagination (offset/limit) |
| `table.filter`         | Filtre / chip |
| `draft.reply`          | Demande de brouillon, **sans** envoi |
| `messages.trash`       | Premier clic → jeton de confirmation ; second clic → corbeille Graph |
| `confirmation.confirm` | Consomme le jeton, exécute la mutation |
| `confirmation.cancel`  | Annule |

Idempotence : clé `workspaceId:idempotencyKey`, TTL 5 min. Un double-clic sur `messages.trash` renvoie le **même** challenge de confirmation.

---

## API

### Backend (Nest, clé API)

- `GET /v1/agent-ui/catalog?workspace_id=`
- `POST /v1/agent-ui/resolve`
- `POST /v1/agent-ui/action`

Le workspace est l’id de compte persisté. Permissions : `mail.read` ; `mail.write` en demo, en test, ou si Outlook a le consentement write.

### BFF Next (`ezer-front`)

- `GET /api/agent-ui/catalog`
- `POST /api/agent-ui/resolve`
- `POST /api/agent-ui/action`

Le BFF retire `userId` / `workspaceId` / `account_id` / `permissions` de l’input, vérifie la liste blanche locale, et proxifie le backend. Sans `EZER_API_URL`, repli démo à partir de `DEMO_ANALYSES`.

---

## Frontend runtime

| Module | Rôle |
|--------|------|
| `lib/agent-ui/contracts.ts` | Parseurs, types discriminés, `protocolVersion` |
| `lib/agent-ui/schema.ts` | Validateur JSON Schema, clés interdites (`__proto__`, `constructor`, `prototype`) |
| `lib/agent-ui/registry.ts` | Seule source `componentId` → React |
| `lib/agent-ui/from-assistant.ts` | `views` bot → `ui.render` |
| `lib/agent-ui/instance-store.ts` | render / patch / remove + `sessionStorage` |
| `lib/agent-ui/markdown.ts` | Liens `https`/`mailto` seulement, détection `<script>` / `eval` / `javascript:` |
| `lib/agent-ui/telemetry.ts` | Événements structurés, champs sensibles non loggés |
| `lib/agent-ui/bff.ts` | Proxy serveur |
| `components/agent-ui/message-renderer.tsx` | Texte vs composant |
| `components/agent-ui/component-renderer.tsx` | Validation, lazy load, skeleton, empty, permission, Error Boundary, abort au démontage |
| `components/agent-ui/error-boundary.tsx` | Un composant cassé n’abat pas la conversation |

États de chargement : `idle`, `loading`, `success`, `empty`, `error`, `permission_denied`, `stale`.

Intégré dans :

- `app/voice-console.tsx` (surface vocale principale) ;
- `app/assistant-panel.tsx` (panneau dashboard).

La confirmation de suppression **déjà** gérée par le bot (`pending_deletions`) n’a pas été dupliquée : le runtime n’y substitue pas un second dialogue.

---

## Bibliothèque proto/v1

Thème : fond `#050505`, accent `#880d1e`, IBM Plex Mono + Instrument Sans (`app/agent-ui.css`).

Fichiers :

- `components/library/primitives.tsx` — carte, pill, tag, avatar
- `components/library/mail.tsx` — liste + détail
- `components/library/metrics.tsx` — carte métrique, brief, expéditeurs
- `components/library/feedback.tsx` — confirmation, vide, skeleton, statut, progression, feed, chips
- `components/library/chrome.tsx` — modes, voix, recherche, menu, réglages, boîtes, brouillon, calendrier, contact, bulles
- `components/library/extended.tsx` — vol, tâche, commande café, playlist, recette, profil, player, achat, annuaire, login, agenda

Galerie : `app/composants/page.tsx` → route `/composants`.

---

## Sécurité

- Listes blanches composants / résolveurs / actions
- Validation stricte de tous les payloads
- Isolation workspace (analyses et comptes filtrés par `account_id`)
- Plafonds de taille (inline 32 Ko, input 8 Ko, body HTTP 64 Ko)
- Timeout résolveur, cache borné
- Pas d’URL agent
- Idempotence anti double-clic
- Confirmation serveur (jeton one-shot, TTL 10 min, `timingSafeEqual`)
- Markdown sanitisé, pas d’`eval` / `new Function` / scripts inline
- Télémétrie sans props/données PII (`componentId` n’est pas masqué par erreur)

---

## Observabilité

Événements : `component_selected`, `component_validation_failed`, `component_render_started`, `component_render_succeeded`, `component_render_failed`, `data_resolver_started`, `data_resolver_succeeded`, `data_resolver_failed`, `ui_action_received`, `ui_action_rejected`, `ui_action_succeeded`.

Champs : `traceId`, `conversationId`, `messageId`, `instanceId`, `componentId`, `componentVersion`, `resolverId`, `durationMs`, `status`.

---

## Scénarios

**A** — « Montre-moi mes vingt dernières factures. »  
`mail.list` + `invoices.search`, pagination serveur, clic → `messages.open`.

**B** — « Quel est mon chiffre d’affaires ce mois-ci ? »  
`metric.card` + `metrics.receipts` (volume de reçus, pas un montant).

**C** — « Supprime ce projet. »  
Pas de mutation immédiate. `confirm.dialog` + jeton. Mutation après `confirmation.confirm`.

**D** — « Explique-moi OAuth 2.0. »  
Message `kind: "text"` uniquement.

---

## Comment étendre

Voir aussi `ezer-front/lib/agent-ui/README.md`.

1. **Composant** : React dans `components/library/` → entrée `registry.ts` (frontend) **et** `catalog.ts` (backend) → `load: () => import("chemin-statique")`.
2. **Résolveur** : `resolvers.ts` (`inputSchema`, `outputSchema`, `requiredPermissions`) → autoriser l’id sur le composant. Ne jamais lire l’identité dans l’input modèle.
3. **Action** : `allowedActions` + handler dans `actions.ts`. Destructive = jeton puis second clic.

Les deux catalogues (front / back) doivent rester alignés.

---

## Tests exécutés

### Backend (`ezer-backend`)

```
npm test   → 46 passed (5 suites)
npm run lint → OK (--max-warnings=0)
npx tsc --noEmit → OK
```

Couvert notamment : catalogue sans chemins d’import, pagination factures, isolation workspace, componentId inconnu, résolveur non autorisé, action inconnue, confirmation + double-clic, rejet de clés `constructor` / propriétés inconnues, clic `messages.open`.

### Frontend (`ezer-front`)

```
pnpm test        → 10 passed
pnpm exec tsc --noEmit → OK
pnpm lint        → OK
```

Couvert notamment : spec valide, componentId inconnu, props invalides, résolveur non autorisé, fallback, `javascript:` / `<script>` / `eval`, action inconnue, restauration historique sans mutation, patch d’instance, strip des champs workspace.

Pas de `next build` dans cette passe (typecheck `tsc` à la place).

---

## Fichiers créés

### Backend

- `ezer-backend/src/agent-ui/contracts.ts`
- `ezer-backend/src/agent-ui/schema.ts`
- `ezer-backend/src/agent-ui/catalog.ts`
- `ezer-backend/src/agent-ui/resolvers.ts`
- `ezer-backend/src/agent-ui/actions.ts`
- `ezer-backend/src/agent-ui/confirmation.ts`
- `ezer-backend/src/agent-ui/cache.ts`
- `ezer-backend/src/agent-ui/errors.ts`
- `ezer-backend/src/agent-ui/telemetry.ts`
- `ezer-backend/src/agent-ui/contracts.spec.ts`
- `ezer-backend/src/api/agent-ui.controller.ts`
- `ezer-backend/src/api/agent-ui.dto.ts`
- `ezer-backend/test/agent-ui.e2e-spec.ts`

### Frontend — runtime

- `ezer-front/lib/agent-ui/contracts.ts`
- `ezer-front/lib/agent-ui/schema.ts`
- `ezer-front/lib/agent-ui/registry.ts`
- `ezer-front/lib/agent-ui/from-assistant.ts`
- `ezer-front/lib/agent-ui/instance-store.ts`
- `ezer-front/lib/agent-ui/markdown.ts`
- `ezer-front/lib/agent-ui/telemetry.ts`
- `ezer-front/lib/agent-ui/bff.ts`
- `ezer-front/lib/agent-ui/runtime.test.ts`
- `ezer-front/lib/agent-ui/README.md`
- `ezer-front/components/agent-ui/message-renderer.tsx`
- `ezer-front/components/agent-ui/component-renderer.tsx`
- `ezer-front/components/agent-ui/error-boundary.tsx`
- `ezer-front/app/api/agent-ui/catalog/route.ts`
- `ezer-front/app/api/agent-ui/resolve/route.ts`
- `ezer-front/app/api/agent-ui/action/route.ts`

### Frontend — librairie proto

- `ezer-front/components/library/primitives.tsx`
- `ezer-front/components/library/agent-props.ts`
- `ezer-front/components/library/mail.tsx`
- `ezer-front/components/library/metrics.tsx`
- `ezer-front/components/library/feedback.tsx`
- `ezer-front/components/library/chrome.tsx`
- `ezer-front/components/library/extended.tsx`
- `ezer-front/app/agent-ui.css`
- `ezer-front/app/composants/page.tsx`

## Fichiers modifiés

- `ezer-backend/src/app.module.ts` — enregistrement du module Agent UI
- `ezer-backend/jest.config.cjs` — `*.spec.ts` en plus des e2e
- `ezer-front/app/layout.tsx` — import `agent-ui.css`
- `ezer-front/app/voice-console.tsx` — rendu Agent UI + actions + restauration
- `ezer-front/app/assistant-panel.tsx` — idem sur le panneau dashboard
- `ezer-front/package.json` — scripts `test` et `typecheck`
- `ezer-front/tsconfig.json` — `allowImportingTsExtensions` (tests Node)

---

## Hors périmètre / non fait

- Pas de transport SSE : le chat reste un POST HTTP unique ; les événements UI sont appliqués après la réponse.
- Les widgets étendus proto ne sont pas sélectionnables par l’agent.
- `draft.reply` ne crée pas de mail : il signale une intention.
- `metrics.receipts` compte des reçus, il n’extrait pas de montants.
- Le contrat `views` du bot Python n’a pas été modifié ; il est adapté côté front.
- `next build` n’a pas été exécuté dans cette passe.

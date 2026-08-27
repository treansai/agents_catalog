# IMPLEMENTED_STATE_02 — L'agent choisit lui-même son composant

Date : 27 août 2026
Périmètre : `ezer-bot` (agent LangChain/Anthropic), `ezer-backend` (NestJS), `ezer-front` (Next.js)
Suite de `IMPLEMENTED_STATE_01.md` — le runtime Agent UI existait, mais **l'agent ne le pilotait
pas** : les composants naissaient d'un effet de bord des outils de lecture (`views`). Désormais
l'agent sélectionne son composant par tool calling natif, et le serveur valide tout.

Ce document décrit ce qui est livré. Ce n'est pas un plan.

---

## Ce qui change

| Avant (état 01) | Maintenant (état 02) |
|---|---|
| `views` déduites des outils de lecture, converties côté front | Outils UI natifs : l'agent choisit `componentId`, version, props, résolveur |
| `instanceId` frappé par le navigateur | `instanceId` frappé par le backend, conservé de bout en bout |
| Pas de mise à jour d'une instance | `ui.patch` / `ui.remove` validés serveur, appliqués à l'instance existante |
| Un clic n'atteignait jamais l'agent | `ui.action` rejoué comme entrée **non fiable**, si le catalogue autorise l'action |
| Prompt sans règles d'interface | 16 règles d'interface + processus de décision fusionnés dans le prompt orchestrateur |

Le contrat de l'état 01 est conservé : agent → `componentId` autorisé → `resolverId` autorisé →
backend valide et charge → front résout dans un registre statique → interaction → `ui.action`
revérifiée → suite métier.

---

## 1. Outils exposés à l'agent

Déclarés dans `ezer-bot/src/ezer/assistant.py` (`_UI_TOOL_SCHEMAS`, agrégés dans
`ALL_TOOL_SCHEMAS`), liés au modèle par `model.bind_tools(...)`. **Tool calling natif Anthropic :
aucun bloc JSON n'est demandé au modèle dans son texte.**

### `get_ui_component_catalog`

```json
{ "query": "tableau de factures", "capabilities": ["display_table"], "limit": 10 }
```

→ `GET /v1/agent-ui/catalog?workspace_id=…&query=…&capabilities=…&limit=…`

Réponse remise au modèle (jamais le code source, jamais un chemin d'import) :

```json
{"components":[{"id":"mail.list","version":"1.0","description":"…",
  "useWhen":["…"],"avoidWhen":["…"],"propsSchema":{},
  "allowedDataResolvers":["invoices.search","messages.search","analyses.triage"],
  "allowedActions":["messages.open","invoices.open","table.page","messages.trash"]}]}
```

Filtrage serveur : permissions de la session (`catalogForPermissions`), puis capacité, puis mots
du libellé (`searchCatalog`). Une recherche sans correspondance renvoie le catalogue permis plutôt
qu'un vide — l'agent ne doit jamais avoir de raison d'inventer.

### `render_ui_component`

```json
{
  "component_id": "mail.list",
  "component_version": "1.0",
  "props": { "title": "Mes dernières factures", "pageSize": 20 },
  "data": { "mode": "resolver", "resolver_id": "invoices.search",
            "input": { "limit": 20, "offset": 0 } },
  "fallback_text": "Je n’ai pas pu afficher la liste des factures."
}
```

→ `POST /v1/agent-ui/render?workspace_id=…`. Le serveur (`ezer-backend/src/agent-ui/render.ts`) :

1. le composant existe (`getComponentDefinition`) ;
2. les permissions requises sont détenues par la session ;
3. la version correspond, sinon `component_version_mismatch` (409) ;
4. `props` validées contre `propsSchema` (`assertSchema`), clés `__proto__`/`constructor` refusées ;
5. source de données analysée par `parseDataSource` : `userId`, `workspaceId`, `permissions`,
   `account_id` **retirés de l'input** ; inline plafonné à 32 Ko, input à 8 Ko ;
6. résolveur ∈ `allowedDataResolvers` du composant ;
7. `instanceId` frappé côté serveur (`ui_<24 hex>`).

Retour au modèle : `{"status":"rendered","instance_id":…,"component_id":…}` — jamais les données.

### `update_ui_component`

```json
{ "instance_id": "ui_…", "patch": { "props": { "selectedInvoiceId": "inv_456" } } }
```

→ `POST /v1/agent-ui/patch`. Le patch est validé contre le `propsSchema` du composant privé de ses
`required` (il est partiel par nature). Une instance inconnue est refusée côté bot : seules les
instances rendues dans le tour, ou annoncées par l'interface dans `ui_instances`, sont patchables.

### `remove_ui_component`

```json
{ "instance_id": "ui_…" }
```

Retire l'instance de la conversation. Refusé si l'instance n'est pas connue.

---

## 2. Aucun JSON de composant dans le texte

Les instructions voyagent dans `AssistantAnswer.ui_messages`, **séparées de `reply`** :

```json
{"kind":"ui.render","protocolVersion":"1.0","id":"ui-…","role":"assistant",
 "createdAt":"…","ui":{"instanceId":"ui_…","componentId":"mail.list"}}
```

Le front les revalide (`parseChatMessage`) dans `lib/ezer-assistant.ts` avant de les rendre. Le
prompt interdit explicitement d'écrire un payload d'outil dans le texte visible.

---

## 3. Sélection du composant

`ORCHESTRATOR_SYSTEM_PROMPT` (version `assistant-agents-ui-2026-08-27.2`) conserve toutes les
règles métier et de sécurité de l'état 01, et ajoute une section « RÈGLES D'INTERFACE » (16 règles)
puis un bloc « DÉCISION » (A→I) : comprendre l'objectif, vérifier si le texte suffit, consulter le
catalogue, choisir par capacité, vérifier schéma/résolveurs/actions, charger des données réelles,
appeler l'outil, fournir un `fallback_text`, attendre l'interaction.

Correspondances retenues, limitées aux composants réellement branchés : plusieurs objets →
`mail.list`, indicateur unique → `metric.card`, classement → `senders.list`, message ouvert →
`mail.detail`, action sensible → `confirm.dialog`, rien à montrer → `empty.state`, filtre court →
`choices.chips`, explication → texte.

---

## 4. Données

- `mode: "resolver"` pour les tableaux, listes paginées, données sensibles ou volumineuses. Les
  données ne transitent **pas** par le contexte du modèle : le composant les charge via
  `POST /api/agent-ui/resolve` (pagination, tri, cache et timeout côté serveur, état 01).
- `mode: "inline"` réservé à quelques valeurs déjà obtenues par un outil, plafonné à 32 Ko.
- L'agent ne fabrique jamais de données : le prompt l'interdit et le résolveur reste la seule
  source pour les listes.

---

## 5. Interactions venant du frontend

`POST /api/ezer/assistant` accepte deux champs supplémentaires, validés strictement par le BFF
(`validateAssistantRequest`) puis par Pydantic (`AssistantRequest`) :

```json
{
  "ui_action": { "kind": "ui.action", "event_id": "…", "message_id": "…", "instance_id": "ui_…",
                 "component_id": "mail.list", "component_version": "1.0",
                 "action_id": "messages.open", "values": { "targetId": "…" },
                 "idempotency_key": "…" },
  "ui_instances": [ { "instance_id": "ui_…", "component_id": "mail.list",
                      "component_version": "1.0" } ]
}
```

Chaîne de vérification avant que l'agent ne voie l'événement :

1. le clic passe d'abord par `POST /api/agent-ui/action` (état 01) : liste blanche, schéma des
   valeurs, permissions, idempotence `workspaceId:idempotencyKey`, confirmation serveur ;
2. le BFF revalide les identifiants et **retire** `userId` / `workspaceId` / `permissions` /
   `account_id` / `credentials` des `values` ;
3. le bot relit le catalogue et refuse l'événement si `action_id` n'est pas dans les
   `allowedActions` du composant — la réponse est alors « Cette action n'est pas autorisée pour ce
   composant », sans tour de modèle ;
4. sinon l'événement entre dans la conversation encadré par `UNTRUSTED_EMAIL_POLICY`
   (`BEGIN/END UNTRUSTED MAILBOX DATA`), comme n'importe quelle donnée non fiable.

Côté interface, la pagination et les filtres (`table.page`, `table.filter`) restent locaux ; seules
`messages.open`, `invoices.open`, `draft.reply`, `form.submit` déclenchent un tour d'agent
(`shouldAskAgent`). Ce tour ne crée pas de message écrit : l'historique peut donc contenir deux
réponses d'assistant de suite, que le bot fusionne (`_conversation`) pour préserver l'alternance
attendue par l'API du modèle.

---

## 6. Confirmations

Aucun troisième mécanisme n'a été introduit. Les deux existants sont conservés et reliés :

- **Action née d'un clic dans un composant** (`messages.trash`) : jeton serveur one-shot,
  `timingSafeEqual`, TTL 10 min, `confirm.dialog` avec `data.mode = inline` porteur du jeton.
- **Suppression née d'une demande à l'agent** : `request_delete_message` ne supprime rien, remplit
  `pending_deletions`, et l'agent affiche `confirm.dialog` (sans jeton). Un clic sur
  `confirmation.confirm` sur ce dialogue rejoint le protocole existant `approved_deletions` — la
  mutation n'a lieu qu'au tour suivant, sur un identifiant que l'opérateur a confirmé.

Un simple « oui » ne suffit toujours pas : la suppression exige l'identifiant confirmé, pas une
phrase.

---

## 7. Erreurs de tool calling

Chaque code renvoie au modèle une consigne courte (`_UI_FAILURE_GUIDANCE`, `_ui_failure`) :

| Code | Consigne donnée à l'agent |
|---|---|
| `unknown_component` | consulter le catalogue, ne pas réessayer avec un autre identifiant inventé |
| `invalid_props` | corriger **une fois** d'après le schéma, sinon répondre en texte |
| `unknown_resolver` | utiliser un résolveur listé ou expliquer l'impossibilité |
| `permission_denied` | informer sans détail interne, ne pas contourner |
| `payload_too_large` | passer en résolveur paginé |
| `component_version_mismatch` | relire le catalogue et reconstruire le payload |
| autre (`backend_unreachable`, `resolver_failed`…) | répondre en texte, ne pas boucler |

Une réponse porte au plus 6 instructions d'interface (`MAX_UI_MESSAGES`) ; au-delà l'agent est
invité à conclure en texte.

---

## 8. Ordre des événements

Décision de l'état 01 conservée : **pas de SSE**. Le chat reste un POST unique. L'ordre exigé est
préservé dans la réponse — `reply` (texte), puis `ui_messages` dans l'ordre d'émission de l'agent
(`ui.render`, puis `ui.patch`), le chargement des données du composant se faisant ensuite côté
interface avec son squelette. Aucun texte du type « je prépare le composant » n'est produit.

---

## 9. Persistance

Persisté (`sessionStorage`, par workspace) : le message, `instanceId`, `componentId`, la version,
les props validées, la référence de données, le `fallbackText`. Au rechargement, les instances sont
reparsées (`parseChatMessage`) et rendues en lecture seule ; aucune mutation n'est rejouée, et un
jeton expiré rend simplement le dialogue inopérant. Ni fonction, ni composant React, ni credential,
ni jeton expiré n'est stocké.

`mergeUiMessages` applique le tour courant : une instance visée par un `ui.patch` est reprise du
tour précédent et mise à jour ; un `ui.remove` la retire ; les instances non visées suivent la
dernière réponse comme auparavant.

---

## 10. Fichiers

### Créés

- `ezer-backend/src/agent-ui/render.ts` — validation `render` / `patch`, frappe de l'`instanceId`
- `ezer-bot/src/ezer/agent_ui.py` — modèles de protocole, `HttpAgentUiClient`, registre d'instances
- `ezer-bot/tests/test_assistant_ui.py` — 17 tests d'agent
- `IMPLEMENTED_STATE_02.md`

### Modifiés

- `ezer-backend/src/agent-ui/catalog.ts` — `searchCatalog` (query / capabilities / limit)
- `ezer-backend/src/agent-ui/errors.ts` — codes `invalid_props`, `component_version_mismatch`
- `ezer-backend/src/api/agent-ui.controller.ts` — `POST /render`, `POST /patch`, catalogue filtrable
- `ezer-backend/src/api/agent-ui.dto.ts` — `RenderBodyDto`, `PatchBodyDto`, `CatalogSearchQueryDto`
- `ezer-backend/test/agent-ui.e2e-spec.ts` — 8 tests supplémentaires
- `ezer-bot/src/ezer/assistant.py` — 4 outils UI, règles d'interface, `ui_messages`, `ui_action`
- `ezer-bot/src/ezer/api.py` — `ui_action` / `ui_instances`, injection du client Agent UI
- `ezer-front/lib/ezer-assistant.ts` — parsing strict de `ui_messages`, validation `ui_action`
- `ezer-front/lib/agent-ui/from-assistant.ts` — priorité aux instructions de l'agent,
  `liveInstances`, `shouldAskAgent`
- `ezer-front/lib/agent-ui/instance-store.ts` — `mergeUiMessages`
- `ezer-front/lib/agent-ui/runtime.test.ts` — 6 tests supplémentaires
- `ezer-front/lib/agent-ui/README.md` — flux mis à jour
- `ezer-front/app/voice-console.tsx`, `ezer-front/app/assistant-panel.tsx` — envoi de
  `ui_instances`, retour d'interaction vers l'agent, confirmation agent reliée au protocole existant

---

## 11. Où regarder

| Question | Fichier |
|---|---|
| Schémas exacts des outils UI | `ezer-bot/src/ezer/assistant.py` → `_UI_TOOL_SCHEMAS` |
| Où le catalogue est injecté | `ezer-bot/src/ezer/assistant.py` → `_ui_catalog` (outil, à la demande) ; source `ezer-backend/src/agent-ui/catalog.ts` |
| Où le system prompt est configuré | `ezer-bot/src/ezer/assistant.py` → `ORCHESTRATOR_SYSTEM_PROMPT` |
| Validation serveur d'un rendu | `ezer-backend/src/agent-ui/render.ts` |
| Transmission au frontend | `AssistantAnswer.ui_messages` → `lib/ezer-assistant.ts` → `messagesFromAssistantAnswer` → `AgentMessageRenderer` |
| Retour d'une interaction | `dispatchUiAction` (front) → `/api/agent-ui/action` → `ui_action` → `_frame_action` (bot) |

---

## 12. Exemples réels

**« Affiche les vingt dernières factures. »**
`get_ui_component_catalog{query:"tableau de factures",capabilities:["display_table"]}` →
`render_ui_component{component_id:"mail.list", props:{title:"Mes dernières factures",pageSize:20},
data:{mode:"resolver",resolver_id:"invoices.search",input:{limit:20,offset:0}}}` → pagination
serveur, clic → `messages.open` → l'agent ouvre le message et répond.

**« Quel est mon volume de reçus ce mois-ci ? »**
`render_ui_component{component_id:"metric.card", props:{label:"Reçus ce mois"},
data:{mode:"resolver",resolver_id:"metrics.receipts",input:{}}}` + une phrase.

**« Supprime ce message. »**
`request_delete_message` (aucune mutation) → `render_ui_component{component_id:"confirm.dialog"}` →
confirmation de l'opérateur → mise à la corbeille au tour suivant → `update_ui_component` sur
l'instance pour afficher le résultat.

**« Qu'est-ce qu'un agent IA ? »**
Aucun outil d'interface. Réponse `kind: "text"`.

---

## 13. Vérifications exécutées

### `ezer-bot`

```
uv run --extra dev python -m pytest        → 156 passed, 1 skipped
uv run --extra dev python -m ruff check    → All checks passed
uv run --extra dev python -m ruff format --check → 45 files already formatted
uv run --extra dev python -m mypy          → Success (strict, 26 fichiers)
```

### `ezer-backend`

```
npm test          → 54 passed (5 suites)
npm run lint      → OK (--max-warnings=0)
npx tsc --noEmit  → OK
```

### `ezer-front`

```
pnpm test              → 16 passed
pnpm exec tsc --noEmit → OK
pnpm lint              → OK
```

Couverture ajoutée : réponse textuelle simple ; tableau pour une liste ; carte métrique pour un
KPI ; confirmation avant suppression ; composant inventé refusé ; props invalides refusées ;
résolveur autorisé / non autorisé ; workspace jamais fourni par le modèle ; clic utilisateur
traité ; action absente du catalogue jamais rejouée ; patch d'une instance vivante ; patch d'une
instance inconnue refusé ; nombre d'instructions borné ; injection de prompt dans un message qui ne
crée aucun composant ; codes d'erreur backend correctement traduits ; conservation de l'`instanceId`
serveur ; `ui.patch` / `ui.remove` appliqués ; instances vivantes annoncées à l'agent ; alternance
stricte des rôles après un retour d'interaction (qui n'ajoute pas de tour écrit).

---

## 14. Hors périmètre / non fait

- Pas de SSE ni d'événements `assistant.text.delta` : transport POST unique, décision de l'état 01.
- Pas de composant formulaire dans le catalogue Ezer : l'action `form.submit` est déjà routée vers
  l'agent, mais aucun composant ne la déclare aujourd'hui. « Crée un nouvel agent » et « compare le
  taux de succès de mes agents » n'ont pas d'équivalent métier ici (Ezer est une boîte mail) ; le
  mapping de l'état 01 reste en vigueur (factures → reçus, CA → volume de reçus, projet → message).
- En mode démonstration (sans `EZER_API_URL` / `EZER_BOT_URL`), les outils UI de l'agent renvoient
  « catalogue indisponible » et l'agent répond en texte ; le repli démo du BFF ne couvre que le
  rendu des composants, pas la sélection par l'agent.
- Pas de graphique ni de timeline : ces composants n'existent pas dans le catalogue, l'agent ne
  peut donc pas les choisir.
- `next build` n'a pas été exécuté dans cette passe (typecheck `tsc` à la place).

# Ezer bot

Assistant multi-agents TypeScript (NestJS). Il orchestre Claude, lit et propose des suppressions
en passant par `ezer-backend`, et n'a jamais de credential Microsoft.

Le bot ne conserve aucune conversation : l'historique est fourni à chaque tour par l'appelant.

## Démarrage local

Prérequis : Node.js 20 ou plus récent.

```bash
cp .env.example .env
npm install
npm run start:dev
```

Renseigner `EZER_API_KEY`, `EZER_ANTHROPIC_API_KEY`, `EZER_BACKEND_URL` et
`EZER_BACKEND_API_KEY`. Sans URL backend, `POST /v1/assistant` répond `422`.

```bash
curl -X POST -H 'Content-Type: application/json' -H 'X-API-Key: ...' \
  -d '{"account_id":"outlook-perso","messages":[{"role":"user","content":"Résume ma boîte."}]}' \
  http://localhost:8081/v1/assistant
```

## API

Les routes métier exigent `X-API-Key: <EZER_API_KEY>` :

```text
GET  /health/live
GET  /health/ready
POST /v1/assistant
```

`POST /v1/assistant` fait tourner trois agents Claude :

- un **orchestrateur** outillé, qui décide quoi lire et quoi proposer ;
- un **agent de synthèse**, sans outil, pour l'état d'ensemble de la boîte ;
- un **agent d'analyse**, sans outil, pour l'intention, la priorité et le risque d'un message.

Outils de l'orchestrateur :

| Outil | Rôle |
| --- | --- |
| `list_recent_messages` | les derniers messages, sans critère |
| `list_messages` | filtre et trie **à la source** : non-lus, expéditeur, fenêtre de dates, ordre |
| `search_messages` | recherche plein texte (objet, expéditeur, contenu) |
| `list_senders` | répartition par expéditeur, du plus prolifique au moins actif |
| `list_triaged` | le triage déjà produit par le pipeline : catégorie, priorité, relecture requise |
| `read_message` | corps complet d'un message |
| `summarize_mailbox` | délégué à l'agent de synthèse |
| `analyze_message` | délégué à l'agent d'analyse |
| `request_delete_message` | *propose* une mise à la corbeille, ne l'exécute jamais seul |
| `get_ui_component_catalog` | composants réellement disponibles pour la session |
| `render_ui_component` | affiche un composant validé par le backend |
| `update_ui_component` | met à jour une instance déjà affichée |
| `remove_ui_component` | retire une instance devenue sans objet |

Ces outils n'accèdent **jamais** à Microsoft : ils appellent `ezer-backend`.

### Suppression : deux temps, jamais un seul

`request_delete_message` ne supprime rien par lui-même. Il inscrit le message dans
`pending_deletions` ; seul un second appel portant `approved_deletions: ["<id>"]` déclenche la
mise à la corbeille.

## Qualité

```bash
npm test
npm run lint
npm run build
```

# Ezer

Ezer est un service multi-agents **strictement en lecture seule** qui synchronise les boîtes Gmail
et Outlook, puis analyse chaque message avec Claude Sonnet 5 et LangGraph. Il détecte d’abord les
contenus hostiles, classe les emails, produit un résumé et extrait les actions explicites. Il ne
peut ni répondre, ni supprimer, ni déplacer un message.

## Architecture

```text
Gmail history.list ─┐
                    ├─> normalisation sûre ─> claim idempotent ─> LangGraph ─> analyse durable
Graph messages/delta┘                              │
                                                  └─> résultats checkpointés chiffrés

LangGraph:  sécurité ─┬─ risque élevé ─> résumé restreint ─┐
                      └─ normal ─> triage ─┬─> résumé ──────┼─> agrégation déterministe
                                          └─> actions ─────┘
```

Les appels LLM sont des agents spécialisés, bornés et sans outil. Les connecteurs, OAuth, le
routage et la persistance restent déterministes. Le corps du message est transmis dans le contexte
d’exécution LangGraph et n’entre pas dans l’état checkpointé. En production, les résultats
structurés sérialisés en blobs sont chiffrés par AES ; les identifiants techniques, versions et
métadonnées primitives de LangGraph restent visibles dans les tables, d’où l’exigence complémentaire
de chiffrement du volume PostgreSQL.

La révision effective du pipeline inclut automatiquement l’identifiant du modèle et la version des
prompts : un changement de modèle ou de prompt crée une nouvelle analyse au lieu de réutiliser un
résultat ancien.

## Démarrage local

Prérequis : Python 3.14, [`uv`](https://docs.astral.sh/uv/) et des identifiants OAuth déjà créés.

```bash
cp .env.example .env
cp accounts.example.json accounts.json
uv sync --all-extras
uv run ezer doctor
uv run ezer sync
uv run ezer api --host 127.0.0.1 --port 8080
```

Dans `.env`, renseigner au minimum `EZER_ANTHROPIC_API_KEY`, `EZER_API_KEY` et
`EZER_ACCOUNTS_FILE=./accounts.json`. Le fichier de comptes contient des secrets : il est ignoré par
Git et doit être monté depuis un gestionnaire de secrets en production.

Le worker continu s’exécute séparément :

```bash
uv run ezer worker
```

## Autorisations email

- Gmail : OAuth délégué avec `https://www.googleapis.com/auth/gmail.readonly` et accès offline.
  `gmail.metadata` ne donne pas accès au corps. Un compte de service ne fonctionne que pour Google
  Workspace avec délégation à l’échelle du domaine et impersonation explicite.
- Outlook : OAuth délégué avec `offline_access Mail.Read`, ou client credentials avec la permission
  application `Mail.Read` et consentement administrateur. Pour l’app-only, restreindre les boîtes
  accessibles avec Exchange Online App RBAC.

Une boîte Microsoft personnelle (`outlook.com`, `hotmail.*`, `live.*`) doit utiliser le flux
délégué et l’autorité `https://login.microsoftonline.com/consumers`. Le flux daemon
`client_credentials` est réservé aux organisations Microsoft Entra et ne peut pas être rendu
compatible avec un compte personnel en changeant simplement l’autorité :

```json
{
  "type": "device_code",
  "client_id": "public-application-client-id"
}
```

Pour une boîte Hotmail personnelle, enregistrer une application Microsoft prenant en charge les
comptes Microsoft personnels, activer les flux de client public et accorder la permission déléguée
Microsoft Graph `Mail.Read`. Après avoir placé son `client_id` public dans `accounts.json`, lancer :

1. Dans Microsoft Entra, ouvrir **App registrations → l’application → Authentication → Advanced
   settings**, puis régler **Allow public client flows** sur **Yes**.
2. Dans **Supported account types**, autoriser les comptes Microsoft personnels.
3. Dans **API permissions**, ajouter uniquement la permission déléguée Microsoft Graph `Mail.Read`.

```bash
uv run ezer auth outlook --account outlook-personal
```

La commande affiche sur le terminal l’URL Microsoft et le code temporaire à saisir. Elle ne démarre
ni Anthropic, ni la base, ni le worker, et n’affiche jamais de jeton. Le cache renouvelable est écrit
dans `EZER_MSAL_CACHE_DIR/<account-id>.bin`. Le worker reste entièrement silencieux : si ce cache est
absent ou expiré, il journalise seulement le code sûr `device_code_login_required`; il ne lance
jamais de parcours interactif.

Les anciennes configurations Ezer qui déclaraient par erreur `client_credentials` pour une adresse
personnelle Microsoft sont reconnues comme `device_code` et réutilisent uniquement le `client_id`.
Le `tenant_id` et le `client_secret` historiques sont ignorés ; migrer néanmoins vers le format
ci-dessus et retirer ce secret devenu inutile.

Les scopes Gmail qui donnent accès au contenu sont restreints ; une application publique doit
suivre la vérification OAuth et, selon son architecture, l’évaluation de sécurité Google. Voir les
[scopes Gmail](https://developers.google.com/workspace/gmail/api/auth/scopes) et les
[permissions Microsoft Graph](https://learn.microsoft.com/en-us/graph/permissions-reference).

Le format complet de configuration est fourni dans [`accounts.example.json`](accounts.example.json).
Les jetons statiques `access_token` ne sont acceptés qu’en développement et sont refusés lorsque
`EZER_ENVIRONMENT=production`.

Le fournisseur OAuth sait remettre un refresh token tournant à un `RefreshTokenSink`. Le bootstrap
CLI standard ne réécrit volontairement jamais `accounts.json` et ne conserve cette rotation qu’en
mémoire. Pour un flux délégué de longue durée en production, injectez une `connector_factory` qui
ferme sur un sink relié au secret manager. Pour les seules boîtes d’organisation, le flux Microsoft
`client_credentials` reste disponible.

## Commandes

```text
ezer doctor                     valide la configuration et la base
ezer auth outlook --account ID  authentifie une boîte Microsoft personnelle
ezer sync [--account ID]        synchronise une page immédiatement
ezer worker [--once]            lance le polling périodique
ezer api [--host H] [--port P]  expose l’API HTTP
```

Les routes métier exigent `X-API-Key: <EZER_API_KEY>` :

```text
GET  /health/live
GET  /health/ready
GET  /metrics
POST /v1/sync
GET  /v1/analyses/{analysis_id}
```

## Production

Le mode production exige PostgreSQL, un checkpointer PostgreSQL distinct ou partagé, une clé AES
de 16/24/32 octets et interdit les jetons statiques. Exemple :

```dotenv
EZER_ENVIRONMENT=production
EZER_DATABASE_URL=postgresql://ezer:...@postgres:5432/ezer
EZER_CHECKPOINT_DATABASE_URL=postgresql://ezer:...@postgres:5432/ezer
EZER_CHECKPOINT_AES_KEY=a-random-32-byte-secret-key-1234
EZER_MSAL_CACHE_DIR=/data/msal-cache
EZER_MSAL_CACHE_AES_KEY=fedcba9876543210fedcba9876543210
```

En production, le cache MSAL doit utiliser un chemin absolu et une clé AES distincte de 16, 24 ou
32 octets. Le Compose monte le même volume chiffré applicativement dans l’API et le worker. Pour
initialiser une boîte personnelle dans ce volume avant de lancer le polling :

```bash
docker compose run --rm --no-deps worker auth outlook --account outlook-personal
```

Les caractères réservés présents dans un mot de passe inclus dans une URL PostgreSQL externe doivent
être encodés en pourcentage. Le Compose évite ce piège en transmettant le secret séparément via
`PGPASSWORD` et en l’omettant des deux URL.

Lancer l’API, le worker et PostgreSQL avec :

```bash
cp accounts.example.json accounts.json  # remplacer tous les placeholders
docker compose up --build
```

Le port de cet exemple est lié uniquement à `127.0.0.1`. Un reverse proxy TLS doit rester le seul
point d’entrée réseau. Le Compose sert de base mono-hôte ; en production managée, utilisez des rôles
PostgreSQL non-superutilisateurs distincts et exigez TLS dans les deux URL de base.

Avant exposition réelle :

- utiliser un secret manager et chiffrer les volumes PostgreSQL au repos ;
- placer l’API derrière TLS et une authentification/gateway adaptée à vos tenants ;
- imposer au gateway une limite de taille de requête et des timeouts adaptés ;
- faire tourner API et worker sous des identités réseau minimales ;
- configurer les budgets et alertes Anthropic/Gmail/Graph ;
- valider le DPA, la résidence et la politique de rétention Anthropic adaptée aux emails traités ;
- sauvegarder PostgreSQL, tester la restauration et définir une politique de rétention ;
- maintenir `LANGGRAPH_STRICT_MSGPACK=true` et ne jamais activer une trace contenant le corps brut ;
- maintenir `LANGSMITH_TRACING=false` et `LANGCHAIN_TRACING_V2=false` ; le démarrage production
  refuse explicitement leur activation afin de ne pas exporter les emails vers une plateforme de trace ;
- préférer une identité managée ou un certificat à un secret client Microsoft lorsque disponible.

Le modèle est `claude-sonnet-5`. Sonnet 5 active le raisonnement adaptatif par défaut et rejette les
valeurs non standards de `temperature`, `top_p` et `top_k`; Ezer n’en transmet aucune. Voir la
[documentation Sonnet 5](https://platform.claude.com/docs/en/models/sonnet-5/whats-new-sonnet-5).

## Garanties et limites de sécurité

- Emails, en-têtes et noms de pièces jointes sont toujours considérés hostiles.
- HTML est converti en texte sans script, style, SVG ni chargement distant.
- Les pièces jointes ne sont jamais téléchargées ; seules leurs métadonnées bornées sont conservées.
- Aucun agent n’a accès à OAuth, au réseau, à une fonction d’envoi ou à un autre compte.
- Les sorties Claude utilisent le JSON Schema natif puis une validation Pydantic.
- Les logs JSON utilisent une liste blanche de champs et excluent sujet, corps, adresses et tokens.
- Le traitement est at-least-once, avec clé stable, lease fenced, dead-letter et curseur avancé
  seulement après traitement durable de la page.
- Un message fournisseur définitivement illisible devient une dead-letter bornée et durable sans
  que son marqueur technique soit jamais envoyé au modèle ; les erreurs 401/403/429/5xx restent des
  erreurs globales et ne sont pas masquées.
- Gmail `historyId` expiré et Graph `deltaLink` invalide déclenchent une resynchronisation bornée.

Le service couvre actuellement le texte de l’Inbox par polling. Les réponses automatiques, le
téléchargement de pièces jointes et la navigation dans les liens sont volontairement hors périmètre.

## Qualité

```bash
uv run pytest
uv run ruff check .
uv run mypy src/ezer
```

Les tests utilisent des transports HTTP et des agents factices ; aucune boîte mail ni clé Anthropic
n’est nécessaire. Les tests d’intégration PostgreSQL sont séparés et doivent être exécutés dans la CI
de déploiement.

## Assistant multi-agents

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

Le filtrage et le tri sont poussés jusqu'à Microsoft Graph (`$filter`, `$orderby`) : « mes non-lus
de Promo depuis août » n'exige pas de rapatrier la boîte pour la trier ensuite. Graph refusant de
combiner `$search` avec `$filter`/`$orderby`, la recherche plein texte reste un outil distinct.
`list_triaged` répond sans relire une seule boîte : il exploite les analyses déjà enregistrées.

Ces outils n'accèdent **jamais** à Microsoft : ils appellent `ezer-backend`, qui détient
l'authentification déléguée et l'accès Graph. Configurez `EZER_BACKEND_URL` et
`EZER_BACKEND_API_KEY` ; sans eux la route répond `422` au lieu d'échouer.

```bash
curl -X POST -H 'Content-Type: application/json' -H 'X-API-Key: ...' \
  -d '{"account_id":"outlook-perso","messages":[{"role":"user","content":"Résume ma boîte."}]}' \
  http://localhost:8081/v1/assistant
```

### Suppression : deux temps, jamais un seul

Le contenu d'un message est une donnée hostile : un expéditeur peut tenter de piloter l'agent.
`request_delete_message` ne supprime donc **rien** par lui-même. Il inscrit le message dans
`pending_deletions`, l'interface affiche objet et expéditeur, et seul un second appel portant
`approved_deletions: ["<id>"]` déclenche la mise à la corbeille. Un message lu ne peut provoquer ni
sa propre suppression ni celle d'un autre.

Tout contenu de boîte remis aux agents est encadré par `UNTRUSTED_EMAIL_POLICY`, la même frontière
que celle du pipeline d'analyse.

L'API du bot ne conserve aucune conversation : l'historique est fourni à chaque tour par l'appelant.

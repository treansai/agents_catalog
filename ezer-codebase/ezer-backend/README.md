# Ezer backend

API NestJS entièrement TypeScript pour le tableau de bord Ezer. Le service lit des messages depuis
un connecteur, produit des analyses déterministes et les conserve dans un fichier JSON écrit
atomiquement.

**Le service n'est plus strictement en lecture seule.** Il expose une mise à la corbeille, appelée
par les agents d'`ezer-bot` après confirmation explicite de l'opérateur. Aucune suppression
définitive n'est exposée, aucun envoi ni aucune modification de message n'est possible, et la portée
OAuth demandée reste bornée à `Mail.ReadWrite`.

## Démarrage rapide

Prérequis : Node.js 20 ou plus récent.

```bash
cp .env.example .env
npm install
npm run start:dev
```

Le mode par défaut est `demo`. Il initialise deux comptes et quatre analyses, puis expose trois
messages supplémentaires lors de la première synchronisation. Sa clé locale par défaut est
`ezer-demo-key` ; configurez toujours `EZER_API_KEY` hors d'un poste de développement.

```bash
curl http://localhost:8080/health/ready
curl -H 'X-API-Key: ezer-demo-key' http://localhost:8080/v1/accounts
curl -H 'X-API-Key: ezer-demo-key' \
  'http://localhost:8080/v1/analyses?limit=20&offset=0&priority=high'
curl -X POST -H 'Content-Type: application/json' \
  -H 'X-API-Key: ezer-demo-key' \
  -d '{"account_ids":["gmail-primary"],"limit":20}' \
  http://localhost:8080/v1/sync
```

## Connecter une boîte Outlook

L'authentification et la lecture des messages sont entièrement assurées par ce backend : le
frontend n'affiche que l'URI publique de Microsoft et le code à saisir, et n'a jamais accès au
`device_code` ni aux jetons.

1. En mode `configured`, déclarez le compte avec sa boîte :
   `EZER_ACCOUNTS_JSON=[{"id":"outlook-perso","provider":"outlook","mailbox":"marctelly@outlook.com"}]`
2. Renseignez `EZER_OUTLOOK_CLIENT_ID` avec une application Entra publique dont l'option
   « Allow public client flows » est activée.
3. Depuis le tableau de bord, cliquez sur « Connecter » : Ezer démarre un flux device code sur
   l'autorité grand public `consumers`, avec les portées `offline_access Mail.Read User.Read`.
4. Ouvrez l'URI affichée, saisissez le code, et connectez-vous **avec la boîte configurée** : une
   autre adresse est refusée avec le code `mailbox_mismatch`.

```bash
curl -X POST -H 'X-API-Key: ...' http://localhost:8080/v1/accounts/outlook-perso/connection
curl -H 'X-API-Key: ...' http://localhost:8080/v1/accounts/outlook-perso/connection
curl -X DELETE -H 'X-API-Key: ...' http://localhost:8080/v1/accounts/outlook-perso/connection
```

Une fois le compte connecté, `/v1/sync` lit la boîte via Microsoft Graph
(`/me/mailFolders/inbox/messages`) au lieu du connecteur catalogue. Le `@odata.nextLink` sert de
curseur et n'est suivi que s'il pointe toujours vers `graph.microsoft.com`. Le refresh token est
conservé dans `EZER_TOKEN_FILE`, écrit atomiquement en `0600`, jamais dans `EZER_DATA_FILE` ni dans
une réponse de l'API. Les échecs sont réduits à des codes stables (`mailbox_mismatch`,
`client_not_configured`, `reauthentication_required`, ...) : aucun message de Microsoft n'est relayé.

## Accès aux messages pour les agents

Les agents vivent dans `ezer-bot`. Ce service est leur **seule** porte d'entrée vers une boîte :
aucun credential Microsoft ne le quitte, et le bot ne parle jamais à Graph.

| Route | Rôle |
| --- | --- |
| `GET /v1/accounts/:id/messages?top=&unread_only=&from_address=&since=&until=&order=` | liste filtrée et triée |
| `GET /v1/accounts/:id/messages?query=` | recherche plein texte (exclusive des filtres, comme l'impose Graph) |
| `GET /v1/accounts/:id/senders?sample=` | répartition des messages récents par expéditeur |
| `GET /v1/accounts/:id/message?message_id=` | corps complet d'un message |
| `GET /v1/accounts/:id/mailbox-stats` | volumes et non-lus par dossier |
| `POST /v1/accounts/:id/message/trash` | déplacement vers la corbeille |

`message/trash` déplace vers « Éléments supprimés » : l'opération reste réversible depuis Outlook,
et aucune suppression définitive n'est exposée. Le protocole de confirmation à deux temps est
appliqué côté bot, qui n'appelle cette route qu'après un accord explicite de l'opérateur.

### Persistance de la connexion

Une boîte se connecte **une seule fois**. Le refresh token est écrit dans `EZER_TOKEN_FILE`
(`/app/data/tokens.json` dans le conteneur, sur le volume `backend-data`) en `0600` : il survit aux
redémarrages, aux `docker compose up --build` et aux changements de version du code. Le jeton
d'accès, lui, n'est gardé qu'en mémoire et se redérive tout seul.

Trois règles rendent cette persistance durable :

1. **Le renouvellement utilise les portées réellement consenties**, mémorisées avec le jeton — pas
   celles que réclame la version courante du code. Élargir les portées n'invalide donc jamais une
   connexion existante : elle continue de fonctionner pour ce qu'elle couvre déjà.
2. **Un échec de renouvellement ne détruit pas le jeton.** Seules les réponses `invalid_grant`,
   `invalid_client` et `unauthorized_client` — c'est-à-dire une révocation explicite par Microsoft —
   effacent la connexion. Un incident réseau, une indisponibilité ou une portée refusée la laissent
   intacte.
3. Un refresh token grand public Microsoft vit **90 jours** et se renouvelle à chaque usage. Tant
   qu'une synchronisation ou une question à l'assistant a lieu dans cette fenêtre, la connexion ne
   se périme pas.

Ce qui impose malgré tout une reconnexion : une révocation depuis le compte Microsoft, un
`docker compose down -v` (qui détruit le volume), la suppression du fichier de jetons, un clic sur
**Déconnecter**, ou l'ajout d'une portée que le consentement précédent ne couvrait pas.

### Consentement requis

La suppression exige la portée `Mail.ReadWrite`. Un compte connecté avant cette évolution ne l'a pas :
`/v1/accounts` renvoie alors `write_enabled: false`, et l'outil échoue avec `write_consent_required`.
Il faut **Déconnecter puis Reconnecter** la boîte une fois pour accorder la nouvelle portée.

## Configuration

Toutes les options sont documentées dans `.env.example`.

- `EZER_MODE=demo` active les comptes, analyses et messages de démonstration. Avec
  `EZER_DEMO_RESET_ON_START=true`, le fichier de données est recréé à chaque démarrage.
- `EZER_MODE=configured` désactive les fixtures. `EZER_ACCOUNTS_JSON` décrit alors les comptes
  publics sous la forme `[{"id":"operations","provider":"outlook"}]`.
- `EZER_SOURCE_FILE` peut pointer vers un tableau JSON de messages (ou `{ "messages": [...] }`).
  Sans ce fichier, les connecteurs configurés retournent simplement une page vide. Cette frontière
  permet d'ajouter un connecteur Gmail, Graph ou webhook sans modifier le service de synchronisation.
  Le connecteur livré lit uniquement ce fichier local : il ne se connecte pas directement aux API
  Gmail ou Microsoft Graph.
- `EZER_DATA_FILE` choisit le fichier de persistance. Chaque mutation est sérialisée, écrite dans un
  fichier temporaire adjacent, synchronisée, puis remplacée avec `rename`.

Le fichier de données contient des synthèses et actions dérivées des messages. Ezer le crée avec des
permissions restrictives, mais un déploiement réel doit aussi le placer sur un volume chiffré et
limiter sa sauvegarde, sa rétention et son accès au seul processus backend.

En mode `configured`, `EZER_API_KEY` est obligatoire. Le serveur ne renvoie jamais la clé, le contenu
des exceptions ou une trace. Toutes les routes `/v1` exigent `X-API-Key`, répondent avec
`Cache-Control: no-store`, propagent un `X-Request-ID` valide ou en créent un, et refusent les corps
supérieurs à 64 Kio.

### Format minimal de `EZER_SOURCE_FILE`

```json
{
  "messages": [
    {
      "account_id": "operations",
      "provider": "outlook",
      "provider_message_id": "message-001",
      "subject": "Validation requise",
      "sender_name": "Équipe opérations",
      "sender_address": "ops@example.test",
      "received_at": "2026-08-27T08:00:00.000Z",
      "body_text": "Merci de valider le planning avant vendredi.",
      "snippet": "Validation du planning avant vendredi"
    }
  ]
}
```

Les identifiants de comptes acceptent uniquement lettres ASCII, chiffres, point, tiret et underscore.
Les fournisseurs reconnus sont `gmail` et `outlook`. Le fichier source est relu à chaque page, ce qui
permet de le mettre à jour sans redémarrer le serveur.

## API

```text
GET  /health/live
GET  /health/ready
GET  /v1/accounts
GET  /v1/analyses?limit=&offset=&account_id=&category=&priority=&needs_human_review=
GET  /v1/analyses/:analysisId
POST /v1/sync                     { account_ids?: string[], limit?: number }
```

La liste d'analyses répond avec `{ items, total, limit, offset }`. La synchronisation répond avec
`{ reports }`; chaque rapport contient les compteurs `fetched`, `processed`, `skipped`, `failed`,
`dead_lettered` et l'état du curseur.

## Qualité

```bash
npm test
npm run lint
npm run build
```

Les tests Jest/Supertest couvrent la santé, l'authentification, la validation, les filtres, la
synchronisation idempotente, les en-têtes de sécurité et la limite de corps.

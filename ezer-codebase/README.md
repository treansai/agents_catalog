# Ezer codebase

Ce dossier regroupe les composants du projet Ezer. La stack active frontend/backend est entièrement
en TypeScript.

## Structure

- `ezer-front/` : interface Next.js et routes BFF de même origine ;
- `ezer-backend/` : API NestJS, analyse des messages et persistance locale ;
- `ezer-bot/` : prototype Python (API + worker LangGraph), conservé comme référence et non utilisé
  par la stack active, mais démarrable via Docker Compose.

## Prérequis

- Node.js 22 ;
- pnpm 9 pour le frontend ;
- npm pour le backend.

## Démarrage avec Docker Compose

L'ensemble de la stack (frontend, backend, bot Python, Postgres) démarre en une commande :

```bash
cd ezer-codebase
cp .env.example .env   # renseigner POSTGRES_PASSWORD et les deux clés AES
docker compose up -d --build
```

| Service | Port hôte | Rôle |
| --- | --- | --- |
| `front` | <http://127.0.0.1:3000> | interface Next.js et routes BFF |
| `backend` | <http://127.0.0.1:8080> | API NestJS (analyse heuristique, persistance JSON) |
| `bot-api` | <http://127.0.0.1:8081> | API FastAPI du prototype Python |
| `postgres` | interne | base du bot Python |

Le frontend joint le backend via le réseau Compose (`EZER_API_URL=http://backend:8080`) : il démarre
donc en mode `live`, pas en mode démo.

Le worker du bot est optionnel car il interroge de vraies boîtes mail et appelle l'API Anthropic :

```bash
docker compose --profile worker up -d
```

Il exige au préalable un cache MSAL amorcé (le compte configuré utilise l'authentification
`device_code`, qui est interactive) :

```bash
docker compose run --rm bot-api auth outlook --account outlook-work
```

Arrêt de la stack :

```bash
docker compose down          # conserve les volumes
docker compose down -v       # supprime aussi les données
```

## Démarrage local (sans Docker)

Dans un premier terminal :

```bash
cd ezer-codebase/ezer-backend
cp .env.example .env
npm install
npm run start:dev
```

Dans un second terminal :

```bash
cd ezer-codebase/ezer-front
cp .env.example .env.local
pnpm install
pnpm dev
```

Le frontend est disponible sur <http://localhost:3000> et le backend sur
<http://localhost:8080>. Les fichiers `.env.example` documentent la configuration locale.

## Commande vocale

Le bouton « Parler » du tableau de bord permet de piloter l'interface à la voix. L'enregistrement
est capté en push-to-talk (un appui pour démarrer, un second pour envoyer, 15 s maximum), converti
en WAV 16 kHz mono dans le navigateur, puis envoyé à la route BFF `/api/ezer/voice`, qui interroge
le modèle audio `gpt-audio-1.5` d'OpenAI côté serveur. La clé API ne quitte jamais le serveur.

Activation — renseignez `OPENAI_API_KEY` dans le `.env` racine (Docker) ou dans
`ezer-front/.env.local` (local), puis redémarrez le service `front`. Sans clé, le bouton reste
visible et la route répond « La commande vocale n'est pas configurée ».

Ordres reconnus :

| Exemple parlé | Effet |
| --- | --- |
| « synchronise ma boîte » / « synchronise gmail » | lance une synchronisation, globale ou ciblée |
| « montre les mails à risque » | bascule la vue sur « À risque » |
| « affiche ceux qui demandent une action » | bascule la vue sur « Avec actions » |
| « filtre sur outlook-ops » | filtre par compte |
| « cherche facture » | remplit la recherche |
| « actualise » | recharge les données sans synchroniser |

Le modèle ne renvoie qu'une intention JSON, qui est revalidée côté serveur puis re-vérifiée côté
client contre la liste réelle des comptes : une réponse inattendue ne peut pas déclencher d'action
hors de ce tableau.

## Cartes et itinéraires

L'assistant peut situer un lieu et proposer un trajet : « trouve le restaurant Le Rival et propose
un trajet à pied ». Il choisit alors le composant `map.route` du catalogue et demande le résolveur
`places.route` ; le géocodage et le calcul d'itinéraire ont lieu côté serveur.

| Variable | Effet |
| --- | --- |
| `EZER_MAPTILER_KEY` | Géocodage (backend) et fond de carte (navigateur). Absente : trajet tracé sur fond uni, lieux limités à une liste connue. |
| `EZER_MAPTILER_STYLE` | Style MapTiler, `streets-v2-dark` par défaut. |
| `EZER_ROUTING_URL` | Service au protocole OSRM. Absente : estimation à vol d'oiseau, annoncée comme telle. |
| `EZER_DEFAULT_ORIGIN` | Point de départ quand l'utilisateur n'en donne pas. |
| `EZER_NOMINATIM_URL` | Géocodeur de repli sans clé, utilisé uniquement sans `EZER_MAPTILER_KEY`. Vide : aucun appel sortant, seuls quelques lieux connus résolvent. |
| `EZER_MAP_CONTACT` | Identifiant envoyé en `User-Agent`, exigé par la politique d'usage de Nominatim. |

La clé MapTiler est lue à l'exécution, pas figée dans l'image : elle se remplace sans reconstruire
le frontend. Elle apparaît dans les requêtes de tuiles du navigateur — restreignez-la par domaine
dans MapTiler. En production, hébergez votre propre service de routage : le serveur public de
démonstration d'OSRM n'offre aucune garantie de disponibilité.

## Vérifications

```bash
cd ezer-codebase/ezer-backend
npm test
npm run lint
npm run build

cd ../ezer-front
pnpm lint
pnpm exec tsc --noEmit
pnpm build
```

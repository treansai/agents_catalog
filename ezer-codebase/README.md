# Ezer codebase

Ce dossier regroupe les composants du projet Ezer. Le frontend est en TypeScript (Next.js), le backend et le bot sont en Python (FastAPI).

## Structure

- `ezer-front/` : interface Next.js et routes BFF de même origine ;
- `ezer-backend/` : API FastAPI, analyse des messages et persistance locale ;
- `ezer-bot/` : assistant multi-agents FastAPI (Claude), qui lit et propose via le backend.

## Prérequis

- Python 3.12 pour le backend et le bot ;
- Node.js 22 et pnpm 9 pour le frontend.

## Démarrage avec Docker Compose

L'ensemble de la stack (frontend, backend, bot) démarre en une commande :

```bash
cd ezer-codebase
cp .env.example .env   # renseigner EZER_BOT_API_KEY et EZER_ANTHROPIC_API_KEY
docker compose up -d --build
```

| Service | Port hôte | Rôle |
| --- | --- | --- |
| `front` | <http://127.0.0.1:3000> | interface Next.js et routes BFF |
| `backend` | <http://127.0.0.1:8080> | API FastAPI (analyse heuristique, persistance JSON) |
| `bot-api` | <http://127.0.0.1:8081> | assistant multi-agents |

Le frontend joint le backend via le réseau Compose (`EZER_API_URL=http://backend:8080`) : il démarre
donc en mode `live`, pas en mode démo. L'assistant joint le bot (`EZER_BOT_URL=http://bot-api:8080`),
qui lui-même joint le backend pour lire et mettre à la corbeille, jamais Microsoft.

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
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
python -m app
```

Dans un second terminal :

```bash
cd ezer-codebase/ezer-bot
cp .env.example .env
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
python -m app
```

Dans un troisième terminal :

```bash
cd ezer-codebase/ezer-front
cp .env.example .env.local
pnpm install
pnpm dev
```

Le frontend est disponible sur <http://localhost:3000>, le backend sur
<http://localhost:8080> et le bot sur <http://localhost:8081>. Les fichiers `.env.example`
documentent la configuration locale.

## Voix

La voix repose sur ElevenLabs : Scribe transcrit, une voix de synthèse lit les réponses. Les deux
surfaces vocales sont en push-to-talk — un appui pour ouvrir le micro, un second pour envoyer,
15 s maximum. L'enregistrement part tel que le navigateur le produit (webm/opus, mp4 sur Safari),
en multipart, sans réencodage. Aucune clé d'API n'atteint le navigateur.

**Console (`/`)** — un tour se déroule en trois temps : `/api/ezer/voice/transcribe` transcrit la
question, `/api/ezer/assistant` la pose à l'agent Ezer, puis `/api/ezer/voice/speak` relaie le flux
audio de la réponse. Ezer est donc seul à raisonner : il n'y a plus de modèle vocal intermédiaire
entre l'utilisateur et l'agent.

**Tableau de bord (`/analyses`)** — le bouton « Parler » pilote l'interface. `/api/ezer/voice`
transcrit l'ordre chez ElevenLabs, puis un modèle de texte OpenAI le réduit à une intention JSON.

Activation — renseignez `ELEVENLABS_API_KEY` (permissions `text_to_speech` **et**
`speech_to_text`) et `OPENAI_API_KEY` dans le `.env` racine (Docker) ou dans
`ezer-front/.env.local` (local), puis redémarrez le service `front`. Sans clé, les boutons restent
visibles et les routes répondent « n'est pas configurée ».

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `ELEVENLABS_VOICE_ID` | `21m00Tcm4TlvDq8ikWAM` | Voix lue (Rachel) |
| `ELEVENLABS_TTS_MODEL` | `eleven_flash_v2_5` | Synthèse ; `eleven_v3` est plus fidèle mais plus lent |
| `ELEVENLABS_STT_MODEL` | `scribe_v1` | Transcription ; `scribe_v2` est plus précis et plus cher |
| `ELEVENLABS_STT_LANGUAGE` | `fra` | Langue attendue ; vide = détection automatique |
| `OPENAI_VOICE_MODEL` | `gpt-4.1-mini` | Lecture des ordres du tableau de bord |

Ordres reconnus par le tableau de bord :

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
pytest
ruff check .

cd ../ezer-bot
pytest
ruff check .

cd ../ezer-front
pnpm lint
pnpm exec tsc --noEmit
pnpm build
```

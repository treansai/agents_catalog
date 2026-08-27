# Ezer Front

Interface Next.js 16 d'Ezer. Le navigateur utilise uniquement les routes de même origine
`/api/ezer/*` : la clé du backend reste dans le runtime serveur Next.js et n'est jamais incluse dans
le bundle client.

Ces routes servent des synthèses potentiellement sensibles. Avant une exposition réseau, placez
l'application derrière l'authentification de votre organisation (SSO, gateway ou reverse proxy) ;
la clé technique du backend protège NestJS, mais ne remplace pas l'authentification des utilisateurs.

## Prérequis

- Node.js 22 ;
- pnpm 9 ;
- le backend NestJS `ezer-backend` pour le mode connecté.

## Démarrage en mode démonstration

Le mode démonstration ne nécessite ni backend ni secret. Les données fournies sont synthétiques et
ne représentent aucune personne ou boîte mail réelle.

```bash
pnpm install
pnpm dev
```

Ouvrez [http://localhost:3000](http://localhost:3000).

## Démarrage avec le backend NestJS

Lancez d'abord `ezer-backend` selon son README, puis créez la configuration locale du frontend :

```bash
cp .env.example .env.local
```

Renseignez les deux variables suivantes :

```dotenv
EZER_API_URL=http://127.0.0.1:8080
EZER_API_KEY=ezer-demo-key
```

`EZER_API_URL` doit désigner l'origine du backend, sans suffixe `/v1`. `EZER_API_KEY` est une
variable privée : ne la préfixez jamais par `NEXT_PUBLIC_` et ne versionnez pas `.env.local`.

Lancez ensuite le frontend :

```bash
pnpm dev
```

Les deux variables doivent être présentes pour activer le mode connecté. Si elles sont absentes, le
frontend utilise son jeu de démonstration. Si elles sont présentes mais que le backend ne répond pas,
le tableau indique `live/unavailable` et ne remplace jamais les données par la démonstration.

## API de même origine

- `GET /api/ezer/snapshot` agrège les comptes et les 100 analyses les plus récentes ;
- `POST /api/ezer/sync` valide puis transmet une demande de synchronisation bornée.

Exemple de synchronisation :

```bash
curl -X POST http://localhost:3000/api/ezer/sync \
  -H 'Content-Type: application/json' \
  --data '{"account_ids":["work-demo"],"limit":50}'
```

Les réponses et les appels au backend utilisent `Cache-Control: no-store`/`cache: "no-store"`.

## Vérifications

```bash
pnpm lint
pnpm exec tsc --noEmit
pnpm build
```

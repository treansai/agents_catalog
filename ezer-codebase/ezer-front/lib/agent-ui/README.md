# Agent UI Runtime

Couche d'intégration isolée : l'agent décrit un composant (`componentId` + `version` + `props` + source de données). Il n'envoie jamais de JSX, d'import, d'URL libre ou de code.

## Flux

1. L'assistant répond en texte (transport HTTP existant `/api/ezer/assistant`).
2. L'agent choisit lui-même son composant : `get_ui_component_catalog`, puis `render_ui_component`,
   `update_ui_component`, `remove_ui_component`. Le backend valide et frappe l'`instanceId` ; les
   instructions arrivent dans `ui_messages`, séparées du texte. Les `views` historiques ne servent
   que de repli quand l'agent n'a rien émis.
3. `AgentMessageRenderer` valide le contrat, charge le composant via le registre statique, puis :
   - `data.mode = inline` : valide et affiche ;
   - `data.mode = resolver` : `POST /api/agent-ui/resolve` (le workspace vient de la session / boîte connectée, jamais du modèle).
4. Un clic émet `ui.action`. Le BFF vérifie la liste blanche, l'idempotence, et les permissions. Une mutation destructive renvoie d'abord `confirm.dialog` avec un jeton serveur.

Catalogue agent : `GET /api/agent-ui/catalog?workspace_id=<compte>` (filtrable par `query`,
`capabilities`, `limit`). Validation d'un affichage : `POST /v1/agent-ui/render` ; d'une mise à
jour : `POST /v1/agent-ui/patch`. Ces deux routes sont appelées par le bot avec la clé d'API
backend, jamais par le navigateur.

Retour d'interaction : une action porteuse de sens (`messages.open`, `invoices.open`,
`draft.reply`, `form.submit`) est renvoyée à l'agent dans le champ `ui_action` du tour suivant,
accompagnée de `ui_instances` (les instances encore affichées, seules cibles autorisées d'un
patch). La pagination et les filtres restent locaux.

## Enregistrer un composant

1. Créer le React dans `components/library/` (pas d'`eval`, pas de `dangerouslySetInnerHTML` sur du contenu modèle).
2. Ajouter une entrée dans `lib/agent-ui/registry.ts` : `id`, `version`, schémas, `allowedDataResolvers`, `allowedActions`, et `load: () => import("chemin-statique")`.
3. Recopier la même définition côté backend dans `ezer-backend/src/agent-ui/catalog.ts`.

Le `load` est une table figée. Interdit : `import(agentProvidedPath)`.

## Enregistrer un résolveur

1. Dans `ezer-backend/src/agent-ui/resolvers.ts`, ajouter un `DataResolverDefinition` (`inputSchema`, `outputSchema`, `requiredPermissions`, timeout/cache déjà appliqués).
2. Ne jamais lire `workspaceId` / `userId` dans l'input modèle.
3. Pas d'URL fournie par l'agent (SSRF).
4. Autoriser l'id dans `allowedDataResolvers` du composant.

## Ajouter une action

1. Déclarer l'id dans `allowedActions` du composant.
2. L'implémenter dans `ezer-backend/src/agent-ui/actions.ts`.
3. Si l'action est destructive : premier appel → jeton de confirmation ; second clic `confirmation.confirm` avec le jeton.

## Carte et itinéraires

`map.route` affiche un lieu et le trajet pour s'y rendre. La chaîne est entièrement serveur :

1. l'agent appelle `render_ui_component{component_id:"map.route", data:{mode:"resolver",
   resolver_id:"places.route", input:{to:"Le Rival, Paris", mode:"walking"}}}` — il n'envoie que du
   texte, jamais une URL ni un hôte ;
2. le résolveur `places.route` géocode le départ et l'arrivée (MapTiler, clé côté backend), calcule
   le tracé sur un service au protocole OSRM (`EZER_ROUTING_URL`), simplifie la polyligne à 500
   points et renvoie `{origin, destination, mode, distanceKm, durationMin, coordinates, estimated}` ;
3. le composant charge MapLibre à la demande et dessine le tracé en accent sur le fond obtenu par
   `GET /api/agent-ui/map-config`.

Sans `EZER_MAPTILER_KEY`, le fond de carte disparaît mais le trajet reste tracé sur fond uni ; sans
`EZER_ROUTING_URL`, la sortie porte `estimated: true` et le composant affiche « estimation à vol
d'oiseau ». Le point de départ par défaut vient de `EZER_DEFAULT_ORIGIN`, jamais du modèle.

## Exemples

Factures (scénario A) :

```json
{
  "kind": "ui.render",
  "ui": {
    "componentId": "mail.list",
    "componentVersion": "1.0",
    "data": { "mode": "resolver", "resolverId": "invoices.search", "input": { "limit": 20, "query": "facture" } },
    "fallbackText": "Voici vos dernières factures."
  }
}
```

Métrique (scénario B) :

```json
{
  "kind": "ui.render",
  "ui": {
    "componentId": "metric.card",
    "componentVersion": "1.0",
    "data": { "mode": "resolver", "resolverId": "metrics.receipts", "input": {} },
    "fallbackText": "Voici le volume de reçus de ce mois."
  }
}
```

Suppression (scénario C) : `messages.trash` n'exécute rien au premier clic. Le serveur renvoie `confirm.dialog` + jeton. La mutation n'a lieu qu'après `confirmation.confirm`.

Texte seul (scénario D) : si aucun composant n'apporte de valeur, l'assistant reste sur un message `kind: "text"`.


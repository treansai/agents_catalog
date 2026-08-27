# Exploitation

## Sémantique de traitement

Chaque page fournisseur est lue au moins une fois. Pour chaque révision de message, Ezer calcule une
identité à partir du compte, du fournisseur, de l’identifiant source, du hash de contenu et de la
version de pipeline. Un worker obtient ensuite un lease fenced ; un autre worker ne peut pas valider
son résultat. Les échecs sont retentés jusqu’au seuil configuré puis placés en dead-letter.

Le curseur de page n’est remplacé qu’après succès ou résolution durable de tous les messages. Les
continuations Gmail/Graph sont stockées comme valeurs opaques et ne sont jamais reconstruites. Les
URLs Graph sont en outre liées exactement à la boîte et au dossier configurés. Ces curseurs ne sont
jamais acceptés depuis l’API publique : protégez néanmoins leur intégrité par les ACL PostgreSQL et
les sauvegardes.

Une réponse 2xx définitivement inexploitable pour un message isolé (payload/MIME invalide ou trop
grand) est enregistrée en dead-letter avant l’avancement du curseur et n’est jamais transmise au
LLM. Le rapport de synchronisation expose `dead_lettered`; les erreurs d’authentification, de quota
ou d’infrastructure restent des échecs de page visibles.

## Modèle de déploiement

Déployer la même image sous deux rôles :

- `ezer api` pour health, métriques, synchronisation administrative et consultation d’analyses ;
- `ezer worker` pour le polling continu.

PostgreSQL contient les curseurs, leases, dead-letters, analyses structurées et checkpoints. Un seul
cluster suffit, mais les deux URLs peuvent pointer vers des bases ou rôles SQL distincts.

## Alertes recommandées

- hausse de `ezer_messages_total{status="failed"}` ou de dead-letters ;
- `401/403` fournisseur, qui signale souvent une réautorisation ou un scope incorrect ;
- `429` répétés et retard de synchronisation supérieur au SLO ;
- refus/troncatures/schema failures du modèle ;
- saturation du pool PostgreSQL et leases expirés ;
- coût/tokens Anthropic et unités Gmail proches des budgets.

## Rotation et incidents

La rotation de `EZER_API_KEY`, des credentials OAuth et de la clé de chiffrement doit se faire depuis
le secret manager. Une rotation de clé de checkpoint nécessite une procédure de migration ou la
purge explicite des checkpoints anciens ; ne changez pas simplement la clé sur une base existante.

Les fournisseurs OAuth acceptent un callback `RefreshTokenSink` pour persister atomiquement un
refresh token tournant. Le bootstrap CLI par défaut ne modifie jamais le fichier de comptes : pour
les credentials délégués en production, injectez une `connector_factory` reliée au secret manager
et faites échouer la rotation si cette écriture durable échoue.

En cas de compromission OAuth : révoquer le credential côté fournisseur, arrêter le worker concerné,
remplacer le secret, auditer les accès et relancer une synchronisation complète. Ezer ne possède
aucune capacité d’écriture dans les boîtes, ce qui limite l’impact mais ne protège pas la
confidentialité des messages déjà accessibles.

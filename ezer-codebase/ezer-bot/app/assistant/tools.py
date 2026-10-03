"""Tool schemas exposed to the orchestrator (Anthropic tool-use format)."""

from typing import Any

MAIL_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_recent_messages",
        "description": "Liste les messages les plus récents de la boîte de réception, du plus récent au plus ancien. Renvoie des en-têtes et un extrait, jamais le corps complet.",
        "input_schema": {
            "type": "object",
            "properties": {"top": {"type": "integer", "description": "Nombre de messages, 1 à 25."}},
            "additionalProperties": False,
        },
    },
    {
        "name": "search_messages",
        "description": "Recherche des messages par mots-clés (objet, expéditeur, contenu).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Termes recherchés."},
                "top": {"type": "integer", "description": "Nombre de résultats, 1 à 25."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_messages",
        "description": "Liste les messages de la boîte de réception avec filtres et tri : non-lus seulement, expéditeur précis, fenêtre de dates, ordre chronologique. À préférer à list_recent_messages dès qu'un critère est donné.",
        "input_schema": {
            "type": "object",
            "properties": {
                "top": {"type": "integer", "description": "Nombre de messages, 1 à 25."},
                "unread_only": {"type": "boolean", "description": "Ne garder que les non-lus."},
                "from_address": {"type": "string", "description": "Adresse exacte de l'expéditeur."},
                "since": {"type": "string", "description": "Date de début, AAAA-MM-JJ."},
                "until": {"type": "string", "description": "Date de fin, AAAA-MM-JJ."},
                "order": {
                    "type": "string",
                    "enum": ["asc", "desc"],
                    "description": "desc = le plus récent d'abord.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "list_senders",
        "description": "Répartit les messages récents par expéditeur, du plus prolifique au moins actif, avec le nombre de non-lus. Pour « qui m'écrit le plus » ou « d'où vient le bruit ».",
        "input_schema": {
            "type": "object",
            "properties": {
                "sample": {"type": "integer", "description": "Taille de l'échantillon, 1 à 25."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "list_triaged",
        "description": "Consulte le triage déjà produit par le pipeline d'analyse : catégorie, priorité et besoin de relecture humaine. Pour « qu'est-ce qui demande une action » ou « montre les messages à risque », sans relire la boîte.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": [
                        "action_required",
                        "informational",
                        "newsletter",
                        "receipt",
                        "security",
                        "spam",
                        "other",
                    ],
                },
                "priority": {"type": "string", "enum": ["low", "normal", "high", "critical"]},
                "needs_human_review": {"type": "boolean"},
                "limit": {"type": "integer", "description": "Nombre d'analyses, 1 à 100."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "read_message",
        "description": "Lit le corps complet d'un message identifié par son id.",
        "input_schema": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "summarize_mailbox",
        "description": "Délègue à l'agent de synthèse un état d'ensemble de la boîte : volumes, non-lus et messages récents.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "analyze_message",
        "description": "Délègue à l'agent d'analyse l'examen d'un message : intention, priorité, actions attendues et risque d'hameçonnage ou d'injection.",
        "input_schema": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "request_delete_message",
        "description": "Demande la mise à la corbeille d'un message. La suppression n'a lieu que si l'opérateur a confirmé ce message précis dans l'interface ; sinon l'outil renvoie une demande de confirmation et rien n'est supprimé.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "reason": {"type": "string", "description": "Motif court, montré à l'opérateur."},
            },
            "required": ["message_id"],
            "additionalProperties": False,
        },
    },
]

UI_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_ui_component_catalog",
        "description": "Liste les composants d'interface réellement disponibles pour cette session, avec leur version, leur schéma de props, leurs résolveurs et leurs actions autorisés. C'est la seule source de vérité : un composant absent d'ici n'existe pas.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Ce que tu cherches à afficher, en langage naturel.",
                },
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Capacités attendues, par exemple display_table, display_metrics, display_document, request_confirmation, display_choices.",
                },
                "limit": {"type": "integer", "description": "Nombre de composants, 1 à 50."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "render_ui_component",
        "description": "Affiche un composant du catalogue dans la conversation. Le serveur vérifie le composant, la version, les props et le résolveur, puis attribue lui-même l'identifiant d'instance. Ne fournis jamais d'identité (userId, workspaceId, permissions) : elle vient de la session.",
        "input_schema": {
            "type": "object",
            "properties": {
                "component_id": {"type": "string", "description": "Identifiant du catalogue."},
                "component_version": {"type": "string", "description": "Version du catalogue."},
                "props": {"type": "object", "description": "Props conformes au schéma publié."},
                "data": {
                    "type": "object",
                    "description": "Source de données. mode=resolver + resolver_id + input pour une donnée réelle, paginée ou sensible ; mode=inline + value pour quelques valeurs courtes déjà obtenues par un outil.",
                    "properties": {
                        "mode": {"type": "string", "enum": ["inline", "resolver"]},
                        "resolver_id": {"type": "string"},
                        "input": {"type": "object"},
                        "value": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
                "fallback_text": {
                    "type": "string",
                    "description": "Phrase à afficher si le composant ne peut pas être rendu.",
                },
            },
            "required": ["component_id", "fallback_text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_ui_component",
        "description": "Met à jour les props d'une instance déjà affichée : statut, filtre, sélection, résultat d'une action. À préférer à un nouvel affichage après une mutation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instance_id": {"type": "string", "description": "Instance renvoyée au rendu."},
                "patch": {
                    "type": "object",
                    "description": "Props à remplacer, sous la clé props.",
                    "properties": {"props": {"type": "object"}},
                    "additionalProperties": False,
                },
            },
            "required": ["instance_id", "patch"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remove_ui_component",
        "description": "Retire une instance devenue sans objet. À n'utiliser que lorsqu'elle n'est plus pertinente.",
        "input_schema": {
            "type": "object",
            "properties": {"instance_id": {"type": "string"}},
            "required": ["instance_id"],
            "additionalProperties": False,
        },
    },
]

ALL_TOOL_SCHEMAS: list[dict[str, Any]] = [*MAIL_TOOLS, *UI_TOOLS]

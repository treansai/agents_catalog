from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends
from starlette.requests import Request
from starlette.responses import Response

from app.domain.models import EMAIL_CATEGORIES, PRIORITIES, Account, AccountSummary, ConnectionState
from app.errors import HttpError
from app.mail.errors import MailConnectionError
from app.services.container import Services
from app.web import validation as v
from app.web.auth import require_api_key
from app.web.responses import json_response

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])

ISO_MESSAGE_ORDER = ("asc", "desc")


def _services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def _failed_connection(account: Account, code: str) -> ConnectionState:
    """Réponse d'échec bornée : jamais un message Microsoft, seulement un code stable d'Ezer."""
    return ConnectionState(
        account_id=account.id,
        provider=account.provider,
        mailbox=account.mailbox,
        status="failed",
        code=code,
        verification_uri=None,
        user_code=None,
        expires_at=None,
        connected_at=None,
        write_enabled=False,
    )


async def _require_account(services: Services, account_id: str) -> Account:
    for raw in await services.persistence.list_accounts():
        if raw["id"] == account_id:
            return Account.model_validate(raw)
    raise HttpError(404)


async def _connection_state(services: Services, account: Account) -> ConnectionState:
    try:
        return await services.outlook_auth.state(account)
    except MailConnectionError as error:
        return _failed_connection(account, error.code)


async def _mail_operation[T](operation: Callable[[], Awaitable[T]]) -> T:
    try:
        return await operation()
    except MailConnectionError as error:
        # Une boîte déconnectée ou un consentement manquant est un refus de la demande.
        raise HttpError(422) from error


@router.get("/accounts")
async def accounts(request: Request) -> Response:
    services = _services(request)
    summaries: list[dict[str, Any]] = []
    for raw in await services.persistence.list_accounts():
        account = Account.model_validate(raw)
        connection = await _connection_state(services, account)
        summaries.append(
            AccountSummary(
                id=account.id,
                provider=account.provider,
                mailbox=connection.mailbox,
                status=connection.status,
                connected_at=connection.connected_at,
                write_enabled=connection.write_enabled,
            ).model_dump(mode="json")
        )
    return json_response({"accounts": summaries})


@router.get("/accounts/{account_id}/connection")
async def connection(account_id: str, request: Request) -> Response:
    services = _services(request)
    v.path_param(account_id, v.ACCOUNT_ID)
    account = await _require_account(services, account_id)
    return json_response((await _connection_state(services, account)).model_dump(mode="json"))


@router.post("/accounts/{account_id}/connection")
async def connect(account_id: str, request: Request) -> Response:
    """Démarre, ou reprend, le flux device code Microsoft pour un compte Outlook configuré."""
    services = _services(request)
    v.path_param(account_id, v.ACCOUNT_ID)
    account = await _require_account(services, account_id)
    try:
        state = await services.outlook_auth.connect(account)
    except MailConnectionError as error:
        state = _failed_connection(account, error.code)
    return json_response(state.model_dump(mode="json"))


@router.delete("/accounts/{account_id}/connection")
async def disconnect(account_id: str, request: Request) -> Response:
    services = _services(request)
    v.path_param(account_id, v.ACCOUNT_ID)
    account = await _require_account(services, account_id)
    await services.outlook_auth.disconnect(account)
    return json_response((await _connection_state(services, account)).model_dump(mode="json"))


@router.get("/analyses")
async def analyses(request: Request) -> Response:
    services = _services(request)
    query = v.query_params(request)
    v.reject_unknown(query, ("limit", "offset", "account_id", "category", "priority", "needs_human_review"))
    limit = v.query_integer(query, "limit", 1, 100, 20)
    offset = v.query_integer(query, "offset", 0, 1_000_000, 0)
    assert limit is not None and offset is not None
    filters = {
        "account_id": v.query_string(query, "account_id", pattern=v.ACCOUNT_ID),
        "category": v.query_choice(query, "category", EMAIL_CATEGORIES),
        "priority": v.query_choice(query, "priority", PRIORITIES),
        "needs_human_review": v.query_boolean(query, "needs_human_review"),
    }
    page = await services.persistence.list_analyses(limit, offset, filters)
    return json_response({**page, "limit": limit, "offset": offset})


@router.get("/analyses/{analysis_id}")
async def analysis(analysis_id: str, request: Request) -> Response:
    services = _services(request)
    v.path_param(analysis_id, v.ANALYSIS_ID)
    found = await services.persistence.get_analysis(analysis_id)
    if found is None:
        raise HttpError(404)
    return json_response(found)


@router.post("/sync")
async def sync(request: Request) -> Response:
    services = _services(request)
    body = v.json_body(request)
    v.reject_unknown(body, ("account_ids", "limit"))
    account_ids = body.get("account_ids")
    if account_ids is not None and (
        not isinstance(account_ids, list)
        or not 1 <= len(account_ids) <= 100
        or len(set(map(str, account_ids))) != len(account_ids)
        or not all(v.is_string(item, pattern=v.ACCOUNT_ID) for item in account_ids)
    ):
        raise v.invalid()
    limit = v.optional_integer(body, "limit", 1, 500)
    try:
        reports = await services.sync.sync(account_ids, limit)
    except MailConnectionError as error:
        # Une boîte réelle déconnectée est un refus de la demande, pas une panne du service.
        raise HttpError(422) from error
    return json_response({"reports": reports})


# Accès direct aux messages d'une boîte connectée. Ces routes sont la seule porte d'entrée des agents
# d'ezer-bot : aucun credential Microsoft ne quitte ce service.


@router.get("/accounts/{account_id}/messages")
async def messages(account_id: str, request: Request) -> Response:
    services = _services(request)
    v.path_param(account_id, v.ACCOUNT_ID)
    query = v.query_params(request)
    v.reject_unknown(query, ("top", "query", "unread_only", "from_address", "since", "until", "order"))
    top = v.query_integer(query, "top", 1, 25, 10)
    assert top is not None
    text = v.query_string(query, "query", min_length=1, max_length=200)
    unread_only = v.query_boolean(query, "unread_only")
    from_address = v.query_string(query, "from_address", pattern=v.SENDER_ADDRESS, max_length=320)
    since = v.query_string(query, "since", pattern=v.ISO_INSTANT, max_length=64)
    until = v.query_string(query, "until", pattern=v.ISO_INSTANT, max_length=64)
    order = v.query_choice(query, "order", ISO_MESSAGE_ORDER)
    account = await _require_account(services, account_id)
    # Graph refuse $search combiné à $filter/$orderby : la recherche exclut donc le tri.
    if text is None:
        found = await _mail_operation(
            lambda: services.graph_mail.list_messages(
                account,
                top=top,
                unread_only=unread_only,
                from_address=from_address,
                since=since,
                until=until,
                order=order,
            )
        )
    else:
        found = await _mail_operation(lambda: services.graph_mail.search(account, text, top))
    return json_response({"messages": found})


@router.get("/accounts/{account_id}/senders")
async def senders(account_id: str, request: Request) -> Response:
    """Répartition des messages récents par expéditeur : le tri « qui m'écrit le plus »."""
    services = _services(request)
    v.path_param(account_id, v.ACCOUNT_ID)
    query = v.query_params(request)
    v.reject_unknown(query, ("sample",))
    sample = v.query_integer(query, "sample", 1, 25, 25)
    assert sample is not None
    account = await _require_account(services, account_id)
    tallies = await _mail_operation(lambda: services.graph_mail.tallies_by_sender(account, sample))
    return json_response({"senders": tallies})


@router.get("/accounts/{account_id}/message")
async def message(account_id: str, request: Request) -> Response:
    services = _services(request)
    v.path_param(account_id, v.ACCOUNT_ID)
    query = v.query_params(request)
    v.reject_unknown(query, ("message_id",))
    message_id = v.query_string(query, "message_id", pattern=v.MESSAGE_ID, required=True)
    assert message_id is not None
    account = await _require_account(services, account_id)
    body = await _mail_operation(lambda: services.graph_mail.get_message(account, message_id))
    return json_response(body)


@router.get("/accounts/{account_id}/mailbox-stats")
async def mailbox_stats(account_id: str, request: Request) -> Response:
    services = _services(request)
    v.path_param(account_id, v.ACCOUNT_ID)
    account = await _require_account(services, account_id)
    stats = await _mail_operation(lambda: services.graph_mail.stats(account))
    return json_response(stats)


@router.post("/accounts/{account_id}/message/trash")
async def trash_message(account_id: str, request: Request) -> Response:
    """Déplacement vers la corbeille : réversible, et jamais une suppression définitive."""
    services = _services(request)
    v.path_param(account_id, v.ACCOUNT_ID)
    body = v.json_body(request)
    v.reject_unknown(body, ("message_id",))
    message_id = v.require_string(body, "message_id", pattern=v.MESSAGE_ID)
    account = await _require_account(services, account_id)
    await _mail_operation(lambda: services.graph_mail.move_to_deleted_items(account, message_id))
    return json_response({"message_id": message_id, "moved_to": "deleteditems"})

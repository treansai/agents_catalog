"""Production read-only Gmail and Microsoft Graph connectors."""

from ezer.connectors.auth import (
    AUTHORITY,
    BearerToken,
    ClientCredentialsTokenProvider,
    RefreshTokenProvider,
    StaticTokenProvider,
    TokenProvider,
    create_token_provider,
)
from ezer.connectors.base import (
    AuthenticationError,
    AuthorizationError,
    ConnectorConfigurationError,
    ConnectorError,
    CursorExpiredError,
    EmailConnector,
    InvalidCursorError,
    MailConnector,
    Provider,
    ProviderResponseError,
    RateLimitError,
    ResourceNotFoundError,
    TransientProviderError,
)
from ezer.connectors.factory import build_connector, create_connector, create_connectors
from ezer.connectors.gmail import GmailConnector
from ezer.connectors.http import AuthorizedHttpClient, RetryPolicy
from ezer.connectors.outlook import OutlookConnector

__all__ = [
    "AUTHORITY",
    "AuthenticationError",
    "AuthorizationError",
    "AuthorizedHttpClient",
    "BearerToken",
    "ClientCredentialsTokenProvider",
    "ConnectorConfigurationError",
    "ConnectorError",
    "CursorExpiredError",
    "EmailConnector",
    "GmailConnector",
    "InvalidCursorError",
    "MailConnector",
    "OutlookConnector",
    "Provider",
    "ProviderResponseError",
    "RateLimitError",
    "RefreshTokenProvider",
    "ResourceNotFoundError",
    "RetryPolicy",
    "StaticTokenProvider",
    "TokenProvider",
    "TransientProviderError",
    "build_connector",
    "create_connector",
    "create_connectors",
    "create_token_provider",
]

"""Persistence and lifecycle management for QuickBooks OAuth tokens.

Tokens are stored in Upstash Redis (accessed over its REST API) rather than
the local filesystem, so the same code works whether the app runs on a
long-lived local process or a stateless serverless platform like Vercel.
Access and refresh token *values* are never logged.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional

import requests
from upstash_redis import Redis

logger = logging.getLogger(__name__)

TOKEN_KEY: str = "qbo:token"
TOKEN_URL: str = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
REQUEST_TIMEOUT: float = 15.0

DEFAULT_ACCESS_TOKEN_TTL_SECONDS: int = 3600
DEFAULT_REFRESH_TOKEN_TTL_SECONDS: int = 100 * 24 * 3600


class TokenStoreError(Exception):
    """Base class for token storage/exchange failures."""


class NoTokenError(TokenStoreError):
    """Raised when no stored token is available."""


class RefreshTokenExpiredError(TokenStoreError):
    """Raised when the refresh token itself has expired; user must reconnect."""


class TokenRefreshFailedError(TokenStoreError):
    """Raised when Intuit rejects a token exchange/refresh request."""


@dataclass
class TokenData:
    access_token: str
    refresh_token: str
    realm_id: str
    access_token_expires_at: float
    refresh_token_expires_at: float

    def is_access_token_expired(self, buffer_seconds: int = 60) -> bool:
        return time.time() >= (self.access_token_expires_at - buffer_seconds)

    def is_refresh_token_expired(self) -> bool:
        return time.time() >= self.refresh_token_expires_at


def _mask(value: str) -> str:
    """Return a short, non-reversible stand-in for a secret value, for logging."""
    if not value:
        return "(empty)"
    return f"{value[:4]}...(len={len(value)})"


_redis_client: Optional[Redis] = None


def _get_redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        url = os.environ.get("UPSTASH_REDIS_REST_URL")
        token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
        if not url or not token:
            raise TokenStoreError(
                "UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN is not set. "
                "Copy .env.example to .env and fill in your Upstash Redis credentials."
            )
        _redis_client = Redis(url=url, token=token)
    return _redis_client


def load_token() -> Optional[TokenData]:
    raw = _get_redis().get(TOKEN_KEY)
    if raw is None:
        return None
    try:
        return TokenData(**json.loads(raw))
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        logger.warning("Stored token is invalid or incomplete (%s); ignoring it", type(exc).__name__)
        return None


def save_token(token: TokenData) -> None:
    _get_redis().set(TOKEN_KEY, json.dumps(asdict(token)))


def clear_token() -> None:
    _get_redis().delete(TOKEN_KEY)


def _post_token_request(client_id: str, client_secret: str, data: dict[str, str]) -> dict[str, Any]:
    try:
        response = requests.post(
            TOKEN_URL,
            data=data,
            auth=(client_id, client_secret),
            headers={"Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.error("Request to Intuit token endpoint failed: %s", type(exc).__name__)
        raise TokenRefreshFailedError("Could not reach Intuit's token endpoint") from exc

    if response.status_code != 200:
        logger.error("Intuit token endpoint returned HTTP %s", response.status_code)
        raise TokenRefreshFailedError(f"Intuit token endpoint returned HTTP {response.status_code}")

    return response.json()


def _token_data_from_payload(
    payload: dict[str, Any], realm_id: str, fallback_refresh_token: Optional[str] = None
) -> TokenData:
    now = time.time()
    access_token = payload["access_token"]
    # Intuit rotates the refresh token on every use; always prefer the new one.
    refresh_token = payload.get("refresh_token") or fallback_refresh_token
    if not refresh_token:
        raise TokenRefreshFailedError("Intuit response did not include a refresh_token")

    expires_in = payload.get("expires_in", DEFAULT_ACCESS_TOKEN_TTL_SECONDS)
    refresh_expires_in = payload.get("x_refresh_token_expires_in", DEFAULT_REFRESH_TOKEN_TTL_SECONDS)

    return TokenData(
        access_token=access_token,
        refresh_token=refresh_token,
        realm_id=realm_id,
        access_token_expires_at=now + expires_in,
        refresh_token_expires_at=now + refresh_expires_in,
    )


def exchange_code(
    code: str, realm_id: str, redirect_uri: str, client_id: str, client_secret: str
) -> TokenData:
    """Exchange an OAuth authorization code for an access/refresh token pair."""
    payload = _post_token_request(
        client_id,
        client_secret,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
    )
    token = _token_data_from_payload(payload, realm_id)
    save_token(token)
    logger.info("Stored new token for realm %s (access_token=%s)", realm_id, _mask(token.access_token))
    return token


def refresh(token: TokenData, client_id: str, client_secret: str) -> TokenData:
    """Use the refresh token to obtain a new access token (and rotated refresh token)."""
    if token.is_refresh_token_expired():
        clear_token()
        raise RefreshTokenExpiredError("Refresh token has expired; user must reconnect")

    payload = _post_token_request(
        client_id,
        client_secret,
        {"grant_type": "refresh_token", "refresh_token": token.refresh_token},
    )
    new_token = _token_data_from_payload(payload, token.realm_id, fallback_refresh_token=token.refresh_token)
    save_token(new_token)
    logger.info("Refreshed access token for realm %s", token.realm_id)
    return new_token


def get_valid_token(client_id: str, client_secret: str) -> TokenData:
    """Return a token guaranteed not to be expired, refreshing proactively if needed.

    Raises NoTokenError if the user has never connected, or
    RefreshTokenExpiredError if reconnection is required.
    """
    token = load_token()
    if token is None:
        raise NoTokenError("No stored token; user has not connected to QuickBooks")

    if token.is_refresh_token_expired():
        clear_token()
        raise RefreshTokenExpiredError("Refresh token has expired; user must reconnect")

    if token.is_access_token_expired():
        try:
            token = refresh(token, client_id, client_secret)
        except TokenRefreshFailedError:
            clear_token()
            raise

    return token

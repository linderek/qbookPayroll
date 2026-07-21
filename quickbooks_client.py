"""Thin wrapper around the QuickBooks Online Accounting REST API.

This module talks to the QuickBooks **Sandbox** environment only. The API
base URL below is a hardcoded constant (never read from configuration) so
there is no code path that could accidentally point requests at production.
"""

from __future__ import annotations

import logging
from typing import Any, Final, Optional
from urllib.parse import urlencode

import requests

from token_store import TokenData

logger = logging.getLogger(__name__)

# Hardcoded on purpose: never configurable via .env, so a tampered or
# misconfigured environment can never redirect calls to production.
SANDBOX_API_BASE: Final[str] = "https://sandbox-quickbooks.api.intuit.com"
AUTHORIZE_URL: Final[str] = "https://appcenter.intuit.com/connect/oauth2"
SCOPE: Final[str] = "com.intuit.quickbooks.accounting"
MINOR_VERSION: Final[str] = "75"
REQUEST_TIMEOUT: Final[float] = 15.0

assert SANDBOX_API_BASE.startswith("https://sandbox-"), "QBO API base must be the sandbox host"


class QBOError(Exception):
    """Base class for QuickBooks API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class QBOBadRequestError(QBOError):
    """HTTP 400."""


class QBOAuthError(QBOError):
    """HTTP 401 (after the automatic refresh-and-retry has been exhausted)."""


class QBOForbiddenError(QBOError):
    """HTTP 403."""


class QBORateLimitError(QBOError):
    """HTTP 429."""

    def __init__(self, message: str, retry_after: Optional[str] = None) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class QBOServerError(QBOError):
    """HTTP 5xx."""


class QBONetworkError(QBOError):
    """Timeout or connection failure reaching QuickBooks."""


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _extract_fault_message(response: requests.Response) -> str:
    try:
        body = response.json()
        errors = body.get("Fault", {}).get("Error", [])
        if errors:
            first = errors[0]
            return f"{first.get('Message', 'Unknown error')}: {first.get('Detail', '')}".strip()
    except (ValueError, AttributeError):
        pass
    return f"HTTP {response.status_code}"


def _handle_response(response: requests.Response) -> dict[str, Any]:
    if response.status_code == 200:
        return response.json()

    logger.warning("QuickBooks API returned HTTP %s for %s", response.status_code, response.url)
    detail = _extract_fault_message(response)

    if response.status_code == 400:
        raise QBOBadRequestError(f"Bad request: {detail}", 400)
    if response.status_code == 401:
        raise QBOAuthError(f"Not authorized: {detail}", 401)
    if response.status_code == 403:
        raise QBOForbiddenError(f"Forbidden: {detail}", 403)
    if response.status_code == 429:
        raise QBORateLimitError("Rate limit exceeded", retry_after=response.headers.get("Retry-After"))
    if response.status_code >= 500:
        raise QBOServerError(f"QuickBooks server error: {detail}", response.status_code)

    raise QBOError(f"Unexpected QuickBooks response: {detail}", response.status_code)


class QuickBooksSession:
    """Executes authenticated requests against the QBO sandbox for one realm."""

    def __init__(self, token: TokenData, client_id: str, client_secret: str) -> None:
        self._token = token
        self._client_id = client_id
        self._client_secret = client_secret

    @property
    def token(self) -> TokenData:
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token.access_token}",
            "Accept": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{SANDBOX_API_BASE}/v3/company/{self._token.realm_id}/{path}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, str]] = None,
        json_body: Optional[dict[str, Any]] = None,
        _retried: bool = False,
    ) -> dict[str, Any]:
        import token_store  # local import avoids a module-level circular dependency

        query = {"minorversion": MINOR_VERSION}
        if params:
            query.update(params)

        headers = self._headers()
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        try:
            response = requests.request(
                method,
                self._url(path),
                headers=headers,
                params=query,
                json=json_body,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.Timeout as exc:
            raise QBONetworkError("Timed out reaching QuickBooks API") from exc
        except requests.RequestException as exc:
            logger.error("QuickBooks API request failed: %s", type(exc).__name__)
            raise QBONetworkError("Could not reach QuickBooks API") from exc

        if response.status_code == 401 and not _retried:
            logger.info("Access token rejected by QuickBooks; refreshing and retrying once")
            self._token = token_store.refresh(self._token, self._client_id, self._client_secret)
            return self._request(method, path, params=params, json_body=json_body, _retried=True)

        return _handle_response(response)

    def get_company_info(self) -> dict[str, Any]:
        realm_id = self._token.realm_id
        data = self._request("GET", f"companyinfo/{realm_id}")
        return data.get("CompanyInfo", {})

    def list_vendors(self) -> list[dict[str, Any]]:
        query = "select * from Vendor where Active = true order by DisplayName"
        data = self._request("GET", "query", params={"query": query})
        return data.get("QueryResponse", {}).get("Vendor", [])

    def list_expense_accounts(self) -> list[dict[str, Any]]:
        query = "select * from Account where AccountType = 'Expense' and Active = true order by Name"
        data = self._request("GET", "query", params={"query": query})
        return data.get("QueryResponse", {}).get("Account", [])

    def create_bill(self, vendor_id: str, account_id: str, amount: float, memo: str = "") -> dict[str, Any]:
        line: dict[str, Any] = {
            "DetailType": "AccountBasedExpenseLineDetail",
            "Amount": amount,
            "AccountBasedExpenseLineDetail": {"AccountRef": {"value": account_id}},
        }
        if memo:
            line["Description"] = memo

        body = {
            "VendorRef": {"value": vendor_id},
            "Line": [line],
        }
        data = self._request("POST", "bill", json_body=body)
        return data.get("Bill", {})

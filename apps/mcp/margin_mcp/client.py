"""HTTP access to the Margin API for the MCP server.

The server never reads the operational database. It asks the API, and the API
applies the same authorization, provenance and audit rules that the web
application is subject to. A request the API refuses is reported as refused,
never quietly trimmed down to what happens to be readable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_TIMEOUT_SECONDS = 60.0


class MarginApiError(RuntimeError):
    """Raised when the Margin API refuses or fails a request."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Margin API returned {status}: {detail}")
        self.status = status
        self.detail = detail

    @property
    def refused(self) -> bool:
        """Whether the API refused the request on access grounds."""

        return self.status in (401, 403)


@dataclass(frozen=True, slots=True)
class MarginIdentity:
    """The identity the server presents to the Margin API.

    A deployment with an identity provider forwards a bearer token. A local
    deployment forwards the header fallback identity. Either way the API
    resolves the principal itself, so the server never decides who the caller
    is.
    """

    token: str = ""
    user: str = ""
    groups: tuple[str, ...] = ()

    def headers(self) -> dict[str, str]:
        if self.token:
            return {"authorization": f"Bearer {self.token}"}
        headers = {"x-margin-user": self.user or "anonymous"}
        if self.groups:
            headers["x-margin-groups"] = ",".join(self.groups)
        return headers


class MarginApiClient:
    """A small HTTP client for the Margin API."""

    def __init__(self, base_url: str, identity: MarginIdentity, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self.identity = identity
        self.timeout = timeout

    def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None
    ) -> Any:
        """Perform one API request and return the decoded payload."""

        url = f"{self.base_url}{path}"
        try:
            response = httpx.request(
                method, url, params=params, json=body, headers=self.identity.headers(), timeout=self.timeout
            )
        except httpx.HTTPError as error:
            raise MarginApiError(0, f"could not reach the Margin API: {error}") from error
        if response.status_code >= 400:
            raise MarginApiError(response.status_code, _detail_of(response))
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text


def describe_refusal(error: MarginApiError) -> str:
    """Explain a refusal without revealing what was withheld.

    A filtered answer would misrepresent what is readable, so a refusal is
    always stated as a refusal.
    """

    if error.refused:
        return (
            "The Margin API refused this request on access grounds. "
            "Nothing was returned, because a filtered answer would misrepresent what is readable. "
            f"({error.detail})"
        )
    return f"The Margin API could not serve this request: {error.detail}"


def _detail_of(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
        if detail is not None:
            return str(detail)
    return response.text[:500]

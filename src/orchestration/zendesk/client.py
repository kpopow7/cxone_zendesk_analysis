from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from orchestration.config import Settings


def _is_retryable_http_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


def require_zendesk_credentials(settings: Settings) -> None:
    missing = [
        name
        for name, value in (
            ("ZENDESK_SUBDOMAIN", settings.zendesk_subdomain),
            ("ZENDESK_EMAIL", settings.zendesk_email),
            ("ZENDESK_API_TOKEN", settings.zendesk_api_token),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


class ZendeskClient:
    """Zendesk REST API client (API token auth)."""

    def __init__(self, settings: Settings) -> None:
        require_zendesk_credentials(settings)
        self._settings = settings
        subdomain = settings.zendesk_subdomain.strip().removesuffix(".zendesk.com")
        base = settings.zendesk_api_base_url
        if base:
            self._base_url = base.rstrip("/")
        else:
            self._base_url = f"https://{subdomain}.zendesk.com"
        self._auth = (f"{settings.zendesk_email}/token", settings.zendesk_api_token)
        self._timeout = settings.request_timeout_seconds

    @property
    def base_url(self) -> str:
        return self._base_url

    def api_url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self._base_url}{path}"

    @retry(
        retry=retry_if_exception(_is_retryable_http_error),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def get(self, path_or_url: str, *, params: dict | None = None) -> httpx.Response:
        url = path_or_url if path_or_url.startswith("http") else self.api_url(path_or_url)
        with httpx.Client(timeout=self._timeout) as client:
            response = client.get(url, auth=self._auth, params=params or {})
            if response.status_code == 429:
                response.raise_for_status()
            if not response.is_success:
                body = response.text.strip()
                if len(body) > 500:
                    body = f"{body[:500]}..."
                raise httpx.HTTPStatusError(
                    f"Zendesk API {response.status_code} for GET {url}"
                    f"{f': {body}' if body else ''}",
                    request=response.request,
                    response=response,
                )
            return response

    def get_json(self, path_or_url: str, *, params: dict | None = None) -> dict:
        return self.get(path_or_url, params=params).json()

    def get_paginated(
        self,
        path: str,
        *,
        collection_key: str,
        params: dict | None = None,
    ) -> list[dict]:
        """Follow Zendesk `next_page` links until exhausted."""
        items: list[dict] = []
        url: str | None = self.api_url(path)
        request_params = params

        while url:
            payload = self.get_json(url, params=request_params)
            request_params = None
            batch = payload.get(collection_key)
            if isinstance(batch, list):
                items.extend(item for item in batch if isinstance(item, dict))
            next_page = payload.get("next_page")
            url = str(next_page) if next_page else None

        return items

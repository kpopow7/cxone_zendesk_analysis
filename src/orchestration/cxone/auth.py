from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import jwt

from orchestration.config import Settings


@dataclass
class CxoneSession:
    access_token: str
    tenant_id: str
    api_base_url: str


class CxoneAuthClient:
    """OAuth2 password-grant authentication and tenant API discovery for CXone."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._discovery_host = settings.cxone_discovery_host.rstrip("/")
        self._token_endpoint: str | None = None
        self._session: CxoneSession | None = None
        self._token_expires_at: float = 0.0

    def get_session(self, *, force_refresh: bool = False) -> CxoneSession:
        if (
            not force_refresh
            and self._session is not None
            and time.time() < self._token_expires_at - 60
        ):
            return self._session

        token_endpoint = self._resolve_token_endpoint()
        token_payload = self._request_token(token_endpoint)
        access_token = token_payload["access_token"]
        tenant_id = self._extract_tenant_id(token_payload)
        api_base_url = self._resolve_api_base_url(tenant_id)

        expires_in = int(token_payload.get("expires_in", 3600))
        self._token_expires_at = time.time() + expires_in
        self._session = CxoneSession(
            access_token=access_token,
            tenant_id=tenant_id,
            api_base_url=api_base_url.rstrip("/"),
        )
        return self._session

    def _resolve_token_endpoint(self) -> str:
        if self._token_endpoint:
            return self._token_endpoint

        url = f"{self._discovery_host}/.well-known/openid-configuration"
        with httpx.Client(timeout=self._settings.request_timeout_seconds) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()

        self._token_endpoint = data["token_endpoint"]
        return self._token_endpoint

    def _request_token(self, token_endpoint: str) -> dict:
        data = {
            "grant_type": "password",
            "client_id": self._settings.cxone_client_id,
            "client_secret": self._settings.cxone_client_secret,
            "username": self._settings.cxone_access_key_id,
            "password": self._settings.cxone_access_key_secret,
        }
        with httpx.Client(timeout=self._settings.request_timeout_seconds) as client:
            response = client.post(
                token_endpoint,
                data=data,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            return response.json()

    def _extract_tenant_id(self, token_payload: dict) -> str:
        id_token = token_payload.get("id_token")
        if not id_token:
            raise ValueError("CXone token response did not include id_token (tenant discovery requires it).")

        claims = jwt.decode(id_token, options={"verify_signature": False})
        tenant_id = claims.get("tenantId") or claims.get("tenant_id")
        if not tenant_id:
            raise ValueError("Could not read tenantId from CXone id_token.")
        return str(tenant_id)

    def _resolve_api_base_url(self, tenant_id: str) -> str:
        if self._settings.cxone_api_base_url:
            return self._settings.cxone_api_base_url.rstrip("/")

        url = f"{self._discovery_host}/.well-known/cxone-configuration"
        with httpx.Client(timeout=self._settings.request_timeout_seconds) as client:
            response = client.get(url, params={"tenantId": tenant_id})
            response.raise_for_status()
            data = response.json()

        if "api_endpoint" in data:
            return str(data["api_endpoint"]).rstrip("/")

        area = data.get("area")
        domain = data.get("domain")
        if area and domain:
            return f"https://api-{area}.{domain}".rstrip("/")

        raise ValueError("CXone discovery response did not include api_endpoint or area/domain.")

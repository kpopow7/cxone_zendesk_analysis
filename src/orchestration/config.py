from datetime import datetime
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # CXone OAuth (back-end app registration + API user access key)
    cxone_client_id: str = Field(..., alias="CXONE_CLIENT_ID")
    cxone_client_secret: str = Field(..., alias="CXONE_CLIENT_SECRET")
    cxone_access_key_id: str = Field(..., alias="CXONE_ACCESS_KEY_ID")
    cxone_access_key_secret: str = Field(..., alias="CXONE_ACCESS_KEY_SECRET")
    cxone_discovery_host: str = Field(
        default="https://cxone.niceincontact.com",
        alias="CXONE_DISCOVERY_HOST",
    )

    # Interaction Analytics API (confirm path via developer portal "Try it out")
    cxone_ia_api_path: str = Field(
        default="/interaction-analytics-gateway/v2",
        alias="CXONE_IA_API_PATH",
    )
  # API uses cursor pagination (links.next), not publishedAfter/publishedBefore on most tenants.
    cxone_ia_date_field: str = Field(default="startTime", alias="CXONE_IA_DATE_FIELD")
    cxone_ia_order: str = Field(default="desc", alias="CXONE_IA_ORDER")
    cxone_ia_page_size: int = Field(default=100, alias="CXONE_IA_PAGE_SIZE")
    cxone_ia_max_pages: int = Field(default=500, alias="CXONE_IA_MAX_PAGES")
    # Comma-separated mediaType filter on list API (empty = all types).
    cxone_phone_media_types: str = Field(
        default="PhoneCall",
        alias="CXONE_PHONE_MEDIA_TYPES",
    )
    # Concurrent per-segment transcript fetches when enrichment is enabled (see --enrich-transcripts).
    cxone_transcript_fetch_concurrency: int = Field(
        default=8,
        alias="CXONE_TRANSCRIPT_FETCH_CONCURRENCY",
    )

    # Optional override when discovery / docs differ for your tenant
    cxone_api_base_url: str | None = Field(default=None, alias="CXONE_API_BASE_URL")

    # Zendesk API token auth (required for Zendesk extract scripts only)
    zendesk_subdomain: str | None = Field(default=None, alias="ZENDESK_SUBDOMAIN")
    zendesk_email: str | None = Field(default=None, alias="ZENDESK_EMAIL")
    zendesk_api_token: str | None = Field(default=None, alias="ZENDESK_API_TOKEN")
    zendesk_api_base_url: str | None = Field(default=None, alias="ZENDESK_API_BASE_URL")
    zendesk_field_map_path: str = Field(
        default="config/zendesk_field_map.json",
        alias="ZENDESK_FIELD_MAP_PATH",
    )
    cxone_zendesk_link_path: str = Field(
        default="config/cxone_zendesk_link.json",
        alias="CXONE_ZENDESK_LINK_PATH",
    )
    interaction_summary_config_path: str = Field(
        default="config/interaction_summary.json",
        alias="INTERACTION_SUMMARY_CONFIG_PATH",
    )
    transcript_summary_config_path: str = Field(
        default="config/transcript_summary.json",
        alias="TRANSCRIPT_SUMMARY_CONFIG_PATH",
    )
    field_normalization_config_path: str = Field(
        default="config/field_normalization.json",
        alias="FIELD_NORMALIZATION_CONFIG_PATH",
    )

    # Step 4 LLM recommendations (optional; uses OpenAI-compatible chat completions API)
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        alias="OPENAI_EMBEDDING_MODEL",
    )
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")

    # PostgreSQL (local Docker default matches docker-compose.yml)
    database_url: str = Field(
        default="postgresql+psycopg://orchestration:orchestration@localhost:5433/orchestration",
        alias="DATABASE_URL",
    )

    # Run behavior
    request_timeout_seconds: float = Field(default=60.0, alias="REQUEST_TIMEOUT_SECONDS")

    @field_validator("cxone_ia_api_path")
    @classmethod
    def normalize_ia_path(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/"):
            value = f"/{value}"
        return value.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def parse_iso_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)

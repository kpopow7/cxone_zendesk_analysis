from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChatbotSettings(BaseSettings):
    """Settings for the hosted analytics chatbot (Railway / local)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(..., alias="DATABASE_URL")
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")

    # Company login: "user1:pass1,user2:pass2" or set CHATBOT_USERNAME + CHATBOT_PASSWORD
    chatbot_users: str | None = Field(default=None, alias="CHATBOT_USERS")
    chatbot_username: str | None = Field(default=None, alias="CHATBOT_USERNAME")
    chatbot_password: str | None = Field(default=None, alias="CHATBOT_PASSWORD")

    chatbot_max_rows: int = Field(default=200, alias="CHATBOT_MAX_ROWS")
    chatbot_query_timeout_seconds: float = Field(default=15.0, alias="CHATBOT_QUERY_TIMEOUT_SECONDS")
    chatbot_show_sql: bool = Field(default=True, alias="CHATBOT_SHOW_SQL")
    chatbot_rag_enabled: bool = Field(default=True, alias="CHATBOT_RAG_ENABLED")
    chatbot_rag_top_k: int = Field(default=8, alias="CHATBOT_RAG_TOP_K")
    chatbot_rag_min_similarity: float = Field(default=0.30, alias="CHATBOT_RAG_MIN_SIMILARITY")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        alias="OPENAI_EMBEDDING_MODEL",
    )
    request_timeout_seconds: float = Field(default=90.0, alias="REQUEST_TIMEOUT_SECONDS")

    # Railway / Gradio bind
    port: int = Field(default=7860, alias="PORT")


@lru_cache
def get_chatbot_settings() -> ChatbotSettings:
    return ChatbotSettings()

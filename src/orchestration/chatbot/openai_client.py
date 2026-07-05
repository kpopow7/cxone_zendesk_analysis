from __future__ import annotations

import httpx


def is_retryable_openai_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


def friendly_openai_error(exc: httpx.HTTPStatusError) -> str:
    code = exc.response.status_code
    if code == 429:
        return (
            "OpenAI rate limit reached (HTTP 429). Wait a minute and try again, or check "
            "usage and billing limits at https://platform.openai.com/usage"
        )
    if code in (401, 403):
        return (
            f"OpenAI rejected the API key (HTTP {code}). Check OPENAI_API_KEY on the chatbot service."
        )
    if code == 404:
        return (
            f"OpenAI model not found (HTTP 404). Check OPENAI_MODEL ({exc.request.url}); "
            "current setting must be available on your account."
        )
    body = exc.response.text.strip()
    if len(body) > 200:
        body = f"{body[:200]}..."
    return f"OpenAI API error (HTTP {code}){f': {body}' if body else ''}"

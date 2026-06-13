from __future__ import annotations

import httpx


def chat_completion_text(
    *,
    prompt: str,
    system_prompt: str,
    api_key: str,
    model: str,
    base_url: str = "https://api.openai.com/v1",
    timeout_seconds: float = 90.0,
    temperature: float = 0.2,
) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        body = response.json()

    try:
        return str(body["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Unexpected LLM API response shape") from exc


def validate_openai_api_key(
    *,
    api_key: str,
    model: str,
    base_url: str = "https://api.openai.com/v1",
    timeout_seconds: float = 30.0,
) -> None:
    """Fail fast when the API key is missing or rejected (HTTP 401/403)."""
    key = api_key.strip()
    if not key:
        raise ValueError("OPENAI_API_KEY is empty")

    url = f"{base_url.rstrip('/')}/chat/completions"
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Reply OK"}],
                "max_tokens": 5,
            },
        )

    if response.status_code in (401, 403):
        raise ValueError(
            "OpenAI API key was rejected (HTTP "
            f"{response.status_code}). Update OPENAI_API_KEY in .env and restart the terminal."
        )
    if response.status_code >= 400:
        body = response.text.strip()
        if len(body) > 200:
            body = f"{body[:200]}..."
        raise ValueError(f"OpenAI API check failed (HTTP {response.status_code}): {body}")


def truncate_text(text: str, max_chars: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 3].rstrip() + "..."

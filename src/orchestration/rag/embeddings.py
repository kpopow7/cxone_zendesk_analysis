from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential


class EmbeddingError(RuntimeError):
    pass


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


@retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
def embed_texts(
    texts: list[str],
    *,
    api_key: str,
    model: str,
    base_url: str = "https://api.openai.com/v1",
    timeout_seconds: float = 90.0,
) -> list[list[float]]:
    if not texts:
        return []

    url = f"{base_url.rstrip('/')}/embeddings"
    payload = {"model": model, "input": texts}
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
        data = body["data"]
        ordered = sorted(data, key=lambda item: item["index"])
        return [item["embedding"] for item in ordered]
    except (KeyError, TypeError, IndexError) as exc:
        raise EmbeddingError("Unexpected embeddings API response shape") from exc


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from orchestration.chatbot.schema_context import build_schema_prompt
from orchestration.chatbot.settings import ChatbotSettings
from orchestration.chatbot.sql_executor import QueryResult, execute_readonly_query, format_results_for_llm
from orchestration.chatbot.sql_guard import validate_sql
from orchestration.db.session import get_engine
from orchestration.rag.retrieve import RetrievedChunk, format_chunks_for_llm, retrieve_knowledge_chunks
from orchestration.rag.router import route_question


def _is_retryable_openai_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


def _friendly_openai_error(exc: httpx.HTTPStatusError) -> str:
    code = exc.response.status_code
    if code == 429:
        return (
            "OpenAI rate limit reached (HTTP 429). Each question uses two API calls "
            "(SQL generation + summary). Wait a minute and try again, or check usage and "
            "billing limits at https://platform.openai.com/usage"
        )
    if code in (401, 403):
        return (
            "OpenAI rejected the API key (HTTP "
            f"{code}). Check OPENAI_API_KEY on the chatbot service."
        )
    if code == 404:
        return (
            f"OpenAI model not found (HTTP 404). Check OPENAI_MODEL "
            f"({exc.request.url}); current setting must be available on your account."
        )
    body = exc.response.text.strip()
    if len(body) > 200:
        body = f"{body[:200]}..."
    return f"OpenAI API error (HTTP {code}){f': {body}' if body else ''}"


@dataclass
class ChatbotResponse:
    answer: str
    sql: str | None = None
    row_count: int | None = None
    error: str | None = None
    mode: str = "sql"
    rag_sources: int = 0
    debug: dict = field(default_factory=dict)


class ChatbotAgent:
    def __init__(self, settings: ChatbotSettings) -> None:
        self._settings = settings
        self._engine = get_engine(settings.database_url)

    def ask(self, question: str, *, history: list[tuple[str, str]] | None = None) -> ChatbotResponse:
        history = history or []
        mode = route_question(question) if self._settings.chatbot_rag_enabled else "sql"

        rag_chunks: list[RetrievedChunk] = []
        if mode in ("rag", "hybrid") and self._settings.openai_api_key:
            try:
                rag_chunks = retrieve_knowledge_chunks(
                    self._engine,
                    question,
                    api_key=self._settings.openai_api_key,
                    embedding_model=self._settings.openai_embedding_model,
                    openai_base_url=self._settings.openai_base_url,
                    top_k=self._settings.chatbot_rag_top_k,
                    min_similarity=self._settings.chatbot_rag_min_similarity,
                    timeout_seconds=self._settings.request_timeout_seconds,
                )
            except Exception as exc:
                if mode == "rag":
                    return ChatbotResponse(
                        answer=(
                            "I could not search call examples for that question. "
                            "Ensure the knowledge index is built "
                            "(`python scripts/build_knowledge_index.py`) and pgvector is enabled. "
                            f"Detail: {exc}"
                        ),
                        error=str(exc),
                        mode=mode,
                    )
                mode = "sql"

        if mode == "rag":
            if not rag_chunks:
                return ChatbotResponse(
                    answer=(
                        "I did not find relevant call examples in the knowledge index. "
                        "Run transcript summarization and "
                        "`python scripts/build_knowledge_index.py`, then try again."
                    ),
                    mode=mode,
                )
            try:
                answer = self._answer_from_rag(question, rag_chunks, history)
            except Exception as exc:
                return ChatbotResponse(answer=_format_agent_error(exc), error=str(exc), mode=mode)
            return ChatbotResponse(answer=answer, mode=mode, rag_sources=len(rag_chunks))

        sql_result: QueryResult | None = None
        sql: str | None = None
        try:
            sql, sql_error = self._generate_sql(question, history)
        except Exception as exc:
            return ChatbotResponse(answer=_format_agent_error(exc), error=str(exc), mode=mode)

        if sql_error and not sql:
            return ChatbotResponse(answer=sql_error, error=sql_error, mode=mode)

        assert sql is not None
        validation = validate_sql(sql, max_limit=self._settings.chatbot_max_rows)
        if not validation.ok:
            return ChatbotResponse(
                answer=f"I could not run that query safely: {validation.error}",
                sql=sql,
                error=validation.error,
                mode=mode,
            )

        try:
            sql_result = execute_readonly_query(
                self._engine,
                validation.sql,
                max_rows=self._settings.chatbot_max_rows,
                timeout_seconds=self._settings.chatbot_query_timeout_seconds,
            )
            sql = validation.sql
        except Exception as exc:
            corrected = self._retry_sql(question, validation.sql, str(exc))
            if corrected:
                validation = validate_sql(corrected, max_limit=self._settings.chatbot_max_rows)
                if validation.ok:
                    try:
                        sql_result = execute_readonly_query(
                            self._engine,
                            validation.sql,
                            max_rows=self._settings.chatbot_max_rows,
                            timeout_seconds=self._settings.chatbot_query_timeout_seconds,
                        )
                        sql = validation.sql
                    except Exception as retry_exc:
                        return ChatbotResponse(
                            answer=f"Query failed: {retry_exc}",
                            sql=validation.sql,
                            error=str(retry_exc),
                            mode=mode,
                        )
                else:
                    return ChatbotResponse(
                        answer=f"Query failed: {exc}",
                        sql=validation.sql,
                        error=str(exc),
                        mode=mode,
                    )
            else:
                return ChatbotResponse(
                    answer=f"Query failed: {exc}",
                    sql=validation.sql,
                    error=str(exc),
                    mode=mode,
                )

        assert sql_result is not None
        try:
            if mode == "hybrid" and rag_chunks:
                answer = self._answer_hybrid(question, sql, sql_result, rag_chunks, history)
            else:
                answer = self._summarize(question, sql, sql_result, history)
        except Exception as exc:
            return ChatbotResponse(
                answer=_format_agent_error(exc),
                sql=sql,
                row_count=sql_result.row_count,
                error=str(exc),
                mode=mode,
                rag_sources=len(rag_chunks),
            )

        if not answer or not answer.strip():
            answer = _format_count_answer(question, sql_result)

        if self._settings.chatbot_show_sql and sql:
            answer = f"{answer}\n\n---\n**SQL used:**\n```sql\n{sql}\n```"

        return ChatbotResponse(
            answer=answer,
            sql=sql,
            row_count=sql_result.row_count,
            mode=mode,
            rag_sources=len(rag_chunks),
        )

    def _answer_from_rag(
        self,
        question: str,
        chunks: list[RetrievedChunk],
        history: list[tuple[str, str]],
    ) -> str:
        prompt = (
            f"Conversation so far:\n{_format_history(history)}\n\n"
            f"User question: {question}\n\n"
            "Relevant call examples retrieved by semantic search:\n"
            f"{format_chunks_for_llm(chunks)}\n\n"
            "Answer as a contact-center analyst. Use the examples to explain patterns, "
            "customer intents, and operational recommendations. "
            "Cite specific skills, reasons, or outcomes from the examples when helpful. "
            "Do not invent calls or facts not supported by the examples."
        )
        return self._chat_completion(
            system="You answer questions using retrieved call interaction examples (RAG).",
            user=prompt,
        )

    def _answer_hybrid(
        self,
        question: str,
        sql: str,
        result: QueryResult,
        chunks: list[RetrievedChunk],
        history: list[tuple[str, str]],
    ) -> str:
        data_preview = format_results_for_llm(result)
        prompt = (
            f"User question: {question}\n\n"
            f"Structured analytics query returned {result.row_count} row(s)"
            f"{' (truncated)' if result.truncated else ''}.\n"
            f"Results JSON:\n{data_preview}\n\n"
            "Relevant call examples from semantic search:\n"
            f"{format_chunks_for_llm(chunks)}\n\n"
            "Write a clear answer for a contact-center manager that combines:\n"
            "1) aggregate metrics from the query results\n"
            "2) contextual insights from the example calls\n"
            "Do not invent data not present in either source."
        )
        return self._chat_completion(
            system="You combine SQL analytics with retrieved call examples.",
            user=prompt,
        )

    def _generate_sql(
        self,
        question: str,
        history: list[tuple[str, str]],
    ) -> tuple[str | None, str | None]:
        history_text = _format_history(history)
        prompt = (
            f"{build_schema_prompt()}\n\n"
            f"Conversation so far:\n{history_text}\n\n"
            f"User question: {question}\n\n"
            "Return ONLY a JSON object: "
            '{"sql": "SELECT ...", "reasoning": "brief note"}'
        )
        content = self._chat_completion(
            system="You write safe PostgreSQL SELECT queries for analytics. JSON only.",
            user=prompt,
        )
        parsed = _parse_json_object(content)
        if not parsed or "sql" not in parsed:
            return None, "I could not generate a valid query for that question. Try rephrasing."
        sql = str(parsed["sql"]).strip()
        return sql, None

    def _retry_sql(self, question: str, failed_sql: str, error: str) -> str | None:
        prompt = (
            f"The following SQL failed.\n\nQuestion: {question}\n\n"
            f"SQL:\n{failed_sql}\n\nError: {error}\n\n"
            f"{build_schema_prompt()}\n\n"
            'Return ONLY JSON: {"sql": "corrected SELECT ..."}'
        )
        content = self._chat_completion(
            system="Fix the PostgreSQL SELECT query. JSON only.",
            user=prompt,
        )
        parsed = _parse_json_object(content)
        if parsed and parsed.get("sql"):
            return str(parsed["sql"]).strip()
        return None

    def _summarize(
        self,
        question: str,
        sql: str,
        result: QueryResult,
        history: list[tuple[str, str]],
    ) -> str:
        if result.row_count == 0:
            return (
                "No matching records were found for that question in the selected time range or filters. "
                "Try widening the date range or relaxing skill/direction filters."
            )

        data_preview = format_results_for_llm(result)
        prompt = (
            f"User question: {question}\n\n"
            f"Query returned {result.row_count} row(s)"
            f"{' (truncated)' if result.truncated else ''}.\n\n"
            f"Results JSON:\n{data_preview}\n\n"
            "Write a clear, concise summary for a contact-center manager. "
            "Use bullet points for rankings. Include counts and percentages where useful. "
            "Do not invent data not present in the results. "
            "If dispositions or reasons look like codes, describe them plainly."
        )
        return self._chat_completion(
            system="You summarize contact center analytics for business users.",
            user=prompt,
        )

    def _chat_completion(self, *, system: str, user: str) -> str:
        url = f"{self._settings.openai_base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self._settings.openai_model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            response = self._post_chat_completion(
                url,
                headers={
                    "Authorization": f"Bearer {self._settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(_friendly_openai_error(exc)) from exc
        body = response.json()
        return str(body["choices"][0]["message"]["content"])

    @retry(
        retry=retry_if_exception(_is_retryable_openai_error),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _post_chat_completion(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict,
    ) -> httpx.Response:
        with httpx.Client(timeout=self._settings.request_timeout_seconds) as client:
            response = client.post(url, headers=headers, json=json)
            if response.status_code in (429, 500, 502, 503, 504):
                response.raise_for_status()
            response.raise_for_status()
            return response


def _format_agent_error(exc: Exception) -> str:
    if isinstance(exc, RuntimeError):
        return str(exc)
    return f"Something went wrong: {exc}. Check DATABASE_URL and OPENAI_API_KEY."


def _format_count_answer(question: str, result: QueryResult) -> str:
    if result.row_count == 1 and result.rows:
        row = result.rows[0]
        if len(row) == 1:
            value = next(iter(row.values()))
            return f"The query returned **{value}**."
    return (
        f"The query returned {result.row_count} row(s), but summarization produced an empty "
        "response. Try again or rephrase your question."
    )


def _format_history(history: list[tuple[str, str]]) -> str:
    if not history:
        return "(none)"
    lines: list[str] = []
    for user_msg, assistant_msg in history[-4:]:
        lines.append(f"User: {user_msg}")
        lines.append(f"Assistant: {assistant_msg[:500]}")
    return "\n".join(lines)


def _parse_json_object(content: str) -> dict | None:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

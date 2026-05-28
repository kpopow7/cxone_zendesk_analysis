from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from orchestration.config import Settings
from orchestration.cxone.auth import CxoneAuthClient, CxoneSession
from orchestration.models import TranscriptRecord


def _is_retryable_http_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


def _raise_cxone_api_error(response: httpx.Response) -> None:
    body = response.text.strip()
    if len(body) > 800:
        body = f"{body[:800]}..."
    hint = ""
    if response.status_code == 401:
        hint = (
            "\nHint: OAuth token expired or invalid. Re-run the extract (token refresh is automatic). "
            "If this persists, verify CXONE_* credentials and IA API scope on the app registration."
        )
    elif response.status_code == 404 and "/interaction-analytics/" in str(response.url):
        hint = (
            "\nHint: wrong IA path. Use CXONE_IA_API_PATH=/interaction-analytics-gateway/v2 "
            "(see NICE 26.1 release notes)."
        )
    raise httpx.HTTPStatusError(
        f"CXone API {response.status_code} for {response.request.method} {response.url}"
        f"{f': {body}' if body else ''}{hint}",
        request=response.request,
        response=response,
    )


class CxoneTranscriptExtractor:
    """Extract analyzed call transcripts from CXone Interaction Analytics."""

    def __init__(self, settings: Settings, auth: CxoneAuthClient) -> None:
        self._settings = settings
        self._auth = auth
        self._phone_media_types = self._parse_phone_media_types(settings.cxone_phone_media_types)

    def extract(
        self,
        start: datetime,
        end: datetime,
        *,
        enrich_transcripts: bool = False,
        limit: int | None = None,
    ) -> list[TranscriptRecord]:
        """Extract via paginated list API; optional concurrent per-page transcript enrichment."""
        records: list[TranscriptRecord] = []

        for page_segments in self._iter_analyzed_segment_pages(start, end):
            segments_with_ids: list[tuple[dict[str, Any], str]] = []
            for segment in page_segments:
                segment_id = self._segment_id(segment)
                if segment_id:
                    segments_with_ids.append((segment, segment_id))

            transcript_by_id: dict[str, dict[str, Any] | None] = {}
            if enrich_transcripts and segments_with_ids:
                segment_ids = [segment_id for _, segment_id in segments_with_ids]
                transcript_by_id = self._fetch_transcripts_concurrent(segment_ids)

            for segment, segment_id in segments_with_ids:
                transcript_payload = transcript_by_id.get(segment_id) if enrich_transcripts else None
                records.append(self._to_record(segment, transcript_payload))
                if limit is not None and len(records) >= limit:
                    return records

        return records

    def _ia_url(self, session: CxoneSession, path: str) -> str:
        base = f"{session.api_base_url}{self._settings.cxone_ia_api_path}"
        return f"{base}{path}"

    def _headers(self, session: CxoneSession) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {session.access_token}",
            "Accept": "application/json",
        }

    def _iter_analyzed_segment_pages(
        self,
        start: datetime,
        end: datetime,
    ) -> Iterator[list[dict[str, Any]]]:
        session = self._auth.get_session()
        url = self._ia_url(session, "/segments/analyzed")
        range_start = self._ensure_utc(start)
        range_end = self._ensure_utc(end)
        order = self._settings.cxone_ia_order.lower()
        # Cursor is epoch-ms; positions the first page near the requested window (API ignores publishedAfter).
        initial_cursor = (
            int(range_end.timestamp() * 1000)
            if order == "desc"
            else int(range_start.timestamp() * 1000)
        )
        params: dict[str, Any] = {
            "pageSize": self._settings.cxone_ia_page_size,
            "dateField": self._settings.cxone_ia_date_field,
            "order": self._settings.cxone_ia_order,
            "cursor": initial_cursor,
        }
        request_url = url
        request_params: dict[str, Any] = params
        pages = 0
        last_cursor: int | None = int(initial_cursor)
        seen_cursors: set[int] = set()

        with httpx.Client(timeout=self._settings.request_timeout_seconds) as client:
            while pages < self._settings.cxone_ia_max_pages:
                pages += 1
                response = self._get_with_retry(
                    client,
                    request_url,
                    request_params,
                )
                payload = response.json()
                page_segments = self._extract_segment_list(payload)
                in_range = [
                    segment
                    for segment in page_segments
                    if self._segment_in_range(segment, range_start, range_end)
                    and self._matches_media_type(segment)
                ]
                if in_range:
                    yield in_range

                if self._should_stop_pagination(page_segments, range_start, range_end):
                    break

                # Some tenants return a `links.previous` URL that repeats the same cursor,
                # causing an infinite loop. Use a cursor derived from the current page's
                # segment timestamps to guarantee forward progress.
                next_cursor = self._derive_next_cursor(page_segments, order)
                if next_cursor is None:
                    next_url = self._next_page_url(payload)
                    if not next_url:
                        break
                    request_url = next_url
                    request_params = {}
                    continue

                if last_cursor is not None and next_cursor == last_cursor:
                    # Ensure progress even when the API returns an inclusive cursor.
                    next_cursor = next_cursor - 1 if order == "desc" else next_cursor + 1

                if next_cursor in seen_cursors:
                    break
                seen_cursors.add(next_cursor)

                last_cursor = next_cursor
                request_url = url
                request_params = dict(params)
                request_params["cursor"] = next_cursor

    def _derive_next_cursor(self, segments: list[dict[str, Any]], order: str) -> int | None:
        """Return an epoch-ms cursor for the next page based on current page timestamps."""
        field = self._settings.cxone_ia_date_field
        times: list[datetime] = []
        for segment in segments:
            dt = self._parse_dt(self._first_str(segment, field, "publishedAt", "startTime"))
            if dt is not None:
                times.append(self._ensure_utc(dt))
        if not times:
            return None
        if order == "desc":
            # Walk backward in time. Subtract 1ms because many tenants treat cursor as inclusive.
            return int(min(times).timestamp() * 1000) - 1
        # Walk forward in time.
        return int(max(times).timestamp() * 1000) + 1

    @retry(
        retry=retry_if_exception(_is_retryable_http_error),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _get_with_retry(
        self,
        client: httpx.Client,
        url: str,
        params: dict[str, Any],
    ) -> httpx.Response:
        return self._get_with_auth_retry(client, url, params=params)

    def _get_with_auth_retry(
        self,
        client: httpx.Client,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """GET with fresh bearer token; refresh once on 401 (expired token mid-run)."""
        session = self._auth.get_session()
        response = client.get(url, headers=self._headers(session), params=params or {})
        if response.status_code == 401:
            session = self._auth.get_session(force_refresh=True)
            response = client.get(url, headers=self._headers(session), params=params or {})
        if response.is_success:
            return response
        _raise_cxone_api_error(response)
        return response  # unreachable

    @retry(
        retry=retry_if_exception(_is_retryable_http_error),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _fetch_transcripts_concurrent(
        self,
        segment_ids: list[str],
    ) -> dict[str, dict[str, Any] | None]:
        """Fetch analyzed-transcript payloads for a page of segments in parallel."""
        concurrency = max(1, self._settings.cxone_transcript_fetch_concurrency)
        workers = min(concurrency, len(segment_ids))
        results: dict[str, dict[str, Any] | None] = {}

        with httpx.Client(timeout=self._settings.request_timeout_seconds) as client:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(self._fetch_analyzed_transcript, segment_id, client): segment_id
                    for segment_id in segment_ids
                }
                for future in as_completed(futures):
                    segment_id = futures[future]
                    results[segment_id] = future.result()

        return results

    def _fetch_analyzed_transcript(
        self,
        segment_id: str,
        client: httpx.Client,
    ) -> dict[str, Any] | None:
        session = self._auth.get_session()
        url = self._ia_url(session, f"/segments/{segment_id}/analyzed-transcript")
        response = client.get(url, headers=self._headers(session))
        if response.status_code == 401:
            session = self._auth.get_session(force_refresh=True)
            response = client.get(url, headers=self._headers(session))
        if response.status_code == 404:
            return None
        if response.is_success:
            return response.json()
        _raise_cxone_api_error(response)
        return None  # unreachable

    @staticmethod
    def _format_api_datetime(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _segment_in_range(
        self,
        segment: dict[str, Any],
        range_start: datetime,
        range_end: datetime,
    ) -> bool:
        field = self._settings.cxone_ia_date_field
        segment_dt = self._parse_dt(self._first_str(segment, field, "publishedAt", "startTime"))
        if segment_dt is None:
            return True
        segment_dt = self._ensure_utc(segment_dt)
        return range_start <= segment_dt <= range_end

    def _should_stop_pagination(
        self,
        page_segments: list[dict[str, Any]],
        range_start: datetime,
        range_end: datetime,
    ) -> bool:
        if not page_segments:
            return True

        times: list[datetime] = []
        field = self._settings.cxone_ia_date_field
        for segment in page_segments:
            dt = self._parse_dt(self._first_str(segment, field, "publishedAt", "startTime"))
            if dt is not None:
                times.append(self._ensure_utc(dt))
        if not times:
            return False

        order = self._settings.cxone_ia_order.lower()
        if order == "desc":
            # Newest first; once entire page is older than range_start, stop.
            return max(times) < range_start
        return min(times) > range_end

    @staticmethod
    def _extract_segment_list(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        if not isinstance(payload, dict):
            return []

        interactions = payload.get("interactions")
        if isinstance(interactions, list):
            return [item for item in interactions if isinstance(item, dict)]

        for key in ("segments", "items", "data", "results", "content"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

        # Older shape: interactions as a map of id -> interaction
        if isinstance(interactions, dict):
            collected: list[dict[str, Any]] = []
            for interaction in interactions.values():
                if isinstance(interaction, dict):
                    nested = interaction.get("segments") or interaction.get("segment")
                    if isinstance(nested, list):
                        collected.extend(item for item in nested if isinstance(item, dict))
                    elif isinstance(nested, dict):
                        collected.append(nested)
            if collected:
                return collected

        return []

    def _next_page_url(self, payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        links = payload.get("links")
        if isinstance(links, dict):
            order = self._settings.cxone_ia_order.lower()
            # order=desc starts at newest; paginate backward via links.previous
            primary = "previous" if order == "desc" else "next"
            for key in (primary, "next", "previous"):
                link = links.get(key)
                if link:
                    return str(link)
        for key in ("nextPageToken", "pageToken", "nextToken", "continuationToken"):
            value = payload.get(key)
            if value:
                return str(value)
        pagination = payload.get("pagination")
        if isinstance(pagination, dict):
            for key in ("nextPageToken", "pageToken", "nextToken", "next"):
                value = pagination.get(key)
                if value:
                    return str(value)
        return None

    @staticmethod
    def _parse_phone_media_types(raw: str) -> frozenset[str]:
        return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())

    def _matches_media_type(self, segment: dict[str, Any]) -> bool:
        if not self._phone_media_types:
            return True
        media_type = (self._first_str(segment, "mediaType", "media_type", "channelType") or "").lower()
        return media_type in self._phone_media_types

    @staticmethod
    def _extract_call_direction(
        segment: dict[str, Any],
        transcript_payload: dict[str, Any] | None,
    ) -> str | None:
        for source in (transcript_payload, segment):
            if not isinstance(source, dict):
                continue
            for key in ("directionType", "directiontype", "direction", "callDirection"):
                value = source.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()
            metrics = source.get("metrics")
            if isinstance(metrics, dict):
                for key in ("directiontype", "directionType", "direction"):
                    value = metrics.get(key)
                    if value is not None and str(value).strip():
                        return str(value).strip()
        return None

    @staticmethod
    def _segment_id(segment: dict[str, Any]) -> str | None:
        for key in ("segmentId", "externalId", "id", "segment_id"):
            value = segment.get(key)
            if value:
                return str(value)
        return None

    def _to_record(
        self,
        segment: dict[str, Any],
        transcript_payload: dict[str, Any] | None,
    ) -> TranscriptRecord:
        segment_id = self._segment_id(segment) or "unknown"

        return TranscriptRecord(
            segment_id=segment_id,
            segment_contact_id=self._first_str(
                segment,
                "segmentContactId",
                "masterExternalId",
                "segment_contact_id",
            ),
            contact_id=self._extract_contact_id(segment, transcript_payload),
            acd_contact_id=self._extract_acd_contact_id(segment, transcript_payload),
            acd_session_id=self._metric_str(segment, transcript_payload, "acdsessionid", "acdSessionId"),
            contact_no=self._extract_contact_no(segment, transcript_payload),
            interaction_start=self._parse_dt(
                self._first_str(
                    segment,
                    "startTime",
                    "interactionStartTime",
                    "publishedAt",
                    "acquiredAt",
                    "start",
                )
            ),
            interaction_end=self._parse_dt(
                self._first_str(segment, "endTime", "interactionEndTime", "publishedAt", "end")
            ),
            agent_name=self._extract_agent_name(segment, transcript_payload),
            team_name=self._extract_team_name(segment, transcript_payload),
            skill_name=self._metric_str(segment, transcript_payload, "skillname", "skillName"),
            ticket_id=self._metric_str(segment, transcript_payload, "ticketId", "ticketid"),
            media_type=self._first_str(segment, "mediaType", "media_type", "channelType"),
            call_direction=self._extract_call_direction(segment, transcript_payload),
            language_code=self._first_str(segment, "languageCode", "language", "lang"),
            client_sentiment=self._channel_sentiment(segment, "CLIENT"),
            agent_sentiment=self._channel_sentiment(segment, "AGENT"),
            segment_summary=self._extract_summary(segment, transcript_payload),
            transcript_text=self._build_transcript_text(segment, transcript_payload),
            raw_metadata={
                "segment": segment,
                **({"transcript": transcript_payload} if transcript_payload else {}),
            },
        )

    def _build_transcript_text(
        self,
        segment: dict[str, Any],
        transcript_payload: dict[str, Any] | None,
    ) -> str:
        if transcript_payload:
            text = self._transcript_from_payload(transcript_payload)
            if text:
                return text

        return self._transcript_from_payload(segment)

    def _transcript_from_payload(self, payload: dict[str, Any]) -> str:
        interwoven = payload.get("interwovenTranscript")
        if isinstance(interwoven, list):
            lines = []
            for block in interwoven:
                if not isinstance(block, dict):
                    continue
                speaker = block.get("channelName") or block.get("speaker") or "UNKNOWN"
                text = block.get("text") or block.get("content") or ""
                if text:
                    lines.append(f"{speaker}: {text}")
            if lines:
                return "\n".join(lines)

        transcript_blocks = payload.get("transcriptBlock") or payload.get("transcriptBlocks")
        if isinstance(transcript_blocks, list):
            lines = []
            for block in transcript_blocks:
                if isinstance(block, dict):
                    speaker = block.get("channelName") or "UNKNOWN"
                    text = block.get("text") or ""
                    if text:
                        lines.append(f"{speaker}: {text}")
            if lines:
                return "\n".join(lines)

        channels = payload.get("channels")
        if isinstance(channels, list):
            parts = []
            for channel in channels:
                if not isinstance(channel, dict):
                    continue
                name = channel.get("name") or channel.get("channelName") or "UNKNOWN"
                text = channel.get("text") or ""
                if text:
                    parts.append(f"=== {name} ===\n{text}")
            if parts:
                return "\n\n".join(parts)

        for key in ("transcript", "transcriptText", "text", "fullTranscript"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        segment_summary = self._extract_summary(payload, None)
        if segment_summary:
            return segment_summary

        return ""

    @staticmethod
    def _extract_summary(
        segment: dict[str, Any],
        transcript_payload: dict[str, Any] | None,
    ) -> str | None:
        for source in (transcript_payload, segment):
            if not source:
                continue
            for key in ("segmentSummary", "autoSummary", "summary"):
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, dict):
                    text = value.get("text") or value.get("summary")
                    if isinstance(text, str) and text.strip():
                        return text.strip()
        return None

    @staticmethod
    def _channel_sentiment(segment: dict[str, Any], channel_name: str) -> str | None:
        channels = segment.get("channels")
        if not isinstance(channels, list):
            return None
        for channel in channels:
            if not isinstance(channel, dict):
                continue
            name = channel.get("name") or channel.get("channelName")
            if str(name).upper() == channel_name.upper():
                sentiment = channel.get("sentiment")
                if isinstance(sentiment, str):
                    return sentiment
                if isinstance(sentiment, dict):
                    return sentiment.get("value") or sentiment.get("label")
        return None

    @staticmethod
    def _metric_str(
        segment: dict[str, Any],
        transcript_payload: dict[str, Any] | None,
        *keys: str,
    ) -> str | None:
        for source in (transcript_payload, segment):
            if not isinstance(source, dict):
                continue
            metrics = source.get("metrics")
            if not isinstance(metrics, dict):
                continue
            for key in keys:
                value = metrics.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()
        return None

    def _extract_agent_name(
        self,
        segment: dict[str, Any],
        transcript_payload: dict[str, Any] | None,
    ) -> str | None:
        return self._first_str(
            segment, "agentName", "agent_name", "primaryAgentName"
        ) or self._metric_str(segment, transcript_payload, "agentname", "agentName")

    def _extract_team_name(
        self,
        segment: dict[str, Any],
        transcript_payload: dict[str, Any] | None,
    ) -> str | None:
        return self._first_str(segment, "teamName", "team_name") or self._metric_str(
            segment, transcript_payload, "teamname", "teamName"
        )

    def _extract_contact_id(
        self,
        segment: dict[str, Any],
        transcript_payload: dict[str, Any] | None,
    ) -> str | None:
        return self._metric_str(segment, transcript_payload, "contactid", "contactId") or self._first_str(
            segment, "contactId", "contact_id"
        )

    def _extract_acd_contact_id(
        self,
        segment: dict[str, Any],
        transcript_payload: dict[str, Any] | None,
    ) -> str | None:
        return self._first_str(segment, "acdContactId", "acd_contact_id") or self._metric_str(
            segment, transcript_payload, "acdcontactid", "acdContactId"
        )

    @staticmethod
    def _extract_contact_no(
        segment: dict[str, Any],
        transcript_payload: dict[str, Any] | None,
    ) -> str | None:
        metrics = (
            transcript_payload.get("metrics")
            if isinstance(transcript_payload, dict)
            else None
        )
        for source in (metrics, segment):
            if not isinstance(source, dict):
                continue
            for key in ("contactNo", "contactno"):
                value = source.get(key)
                if isinstance(value, list):
                    for item in value:
                        if item is not None and str(item).strip():
                            return str(item).strip()
                elif value is not None and str(value).strip():
                    return str(value).strip()
        return None

    @staticmethod
    def _first_str(data: dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = data.get(key)
            if value is not None and value != "":
                return str(value)
        return None

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            normalized = value.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None


def records_to_json(records: list[TranscriptRecord], *, indent: int = 2) -> str:
    from dataclasses import asdict

    return json.dumps([asdict(record) for record in records], indent=indent, default=str)

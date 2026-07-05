from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from orchestration.analysis.reason_taxonomy import ReasonTaxonomy
from orchestration.chatbot.agent_state import AgentRunState
from orchestration.chatbot.settings import ChatbotSettings
from orchestration.chatbot.tools.analytics import run_analytics_sql
from orchestration.chatbot.tools.entities import list_catalog, resolve_entities
from orchestration.chatbot.tools.rag import search_interactions
from orchestration.chatbot.tools.reductions import get_reduction_recommendations
from sqlalchemy.engine import Engine


@dataclass
class ToolContext:
    engine: Engine
    settings: ChatbotSettings
    known_skills: Callable[[], list[str]]
    known_form_names: Callable[[], list[str]]
    reason_taxonomy: Callable[[], ReasonTaxonomy]
    contextual_question: str


def execute_tool(name: str, arguments: dict[str, Any], ctx: ToolContext, state: AgentRunState) -> dict[str, Any]:
    """Dispatch a tool call from the ReAct loop."""
    args = arguments or {}

    if name == "resolve_entities":
        return resolve_entities(
            engine=ctx.engine,
            form_hints=args.get("form_hints"),
            skill_hints=args.get("skill_hints"),
            reason_hints=args.get("reason_hints"),
            known_form_names=ctx.known_form_names(),
            known_skills=ctx.known_skills(),
            taxonomy=ctx.reason_taxonomy(),
        )

    if name == "list_catalog":
        return list_catalog(
            engine=ctx.engine,
            dimension=str(args.get("dimension", "")),
            known_form_names=ctx.known_form_names(),
            known_skills=ctx.known_skills(),
            taxonomy=ctx.reason_taxonomy(),
            limit=int(args.get("limit") or 50),
        )

    if name == "run_analytics_sql":
        forms = _merged_form_names(state, args.get("form_names"))
        return run_analytics_sql(
            engine=ctx.engine,
            settings=ctx.settings,
            intent=args.get("intent"),
            sql=args.get("sql"),
            form_names=forms,
            skill_names=_merge_list(state.resolved.skill_names, args.get("skill_names")),
            canonical_reasons=_merge_list(
                state.resolved.canonical_reasons, args.get("canonical_reasons")
            ),
            media_types=args.get("media_types"),
            days=args.get("days"),
            limit=int(args.get("limit") or 20),
            inbound_only=bool(args.get("inbound_only", True)),
            dimension=args.get("dimension"),
            reason_filter=args.get("reason_filter"),
        )

    if name == "search_interactions":
        question = str(args.get("question") or ctx.contextual_question)
        return search_interactions(
            engine=ctx.engine,
            settings=ctx.settings,
            question=question,
            embed_query=ctx.contextual_question,
            known_skills=ctx.known_skills(),
            taxonomy=ctx.reason_taxonomy(),
            skill_name=args.get("skill_name"),
            canonical_reason=args.get("canonical_reason"),
            top_k=args.get("top_k"),
        )

    if name == "get_reduction_recommendations":
        return get_reduction_recommendations(
            engine=ctx.engine,
            reasons=args.get("reasons"),
            limit=int(args.get("limit") or 10),
        )

    return {"error": f"Unknown tool: {name}"}


def _merged_form_names(state: AgentRunState, explicit: Any) -> list[str]:
    ui_and_resolved = state.effective_form_names()
    extra = [str(x) for x in (explicit or []) if x]
    return _merge_list(ui_and_resolved, extra)


def _merge_list(base: list[str], extra: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in list(base) + [str(x) for x in (extra or []) if x]:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def serialize_tool_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, default=str)

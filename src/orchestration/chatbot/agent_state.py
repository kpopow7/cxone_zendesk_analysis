from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResolvedEntities:
    """Exact database values resolved from fuzzy user mentions."""

    form_names: list[str] = field(default_factory=list)
    skill_names: list[str] = field(default_factory=list)
    canonical_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "form_names": self.form_names,
            "skill_names": self.skill_names,
            "canonical_reasons": self.canonical_reasons,
        }


@dataclass
class ToolResult:
    """One tool invocation during an agent run."""

    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "result": self.result,
            "error": self.error,
        }


@dataclass
class AgentRunState:
    """Mutable state accumulated across ReAct steps."""

    question: str
    memory_context: str
    ui_form_types: list[str] | None
    resolved: ResolvedEntities = field(default_factory=ResolvedEntities)
    tool_results: list[ToolResult] = field(default_factory=list)
    steps_taken: int = 0

    def record(self, tool: str, arguments: dict[str, Any], result: dict[str, Any], *, error: str | None = None) -> None:
        self.tool_results.append(
            ToolResult(tool=tool, arguments=arguments, result=result, error=error)
        )
        self.steps_taken += 1
        if tool == "resolve_entities" and not result.get("error"):
            self.resolved = ResolvedEntities(
                form_names=list(result.get("form_names") or []),
                skill_names=list(result.get("skill_names") or []),
                canonical_reasons=list(result.get("canonical_reasons") or []),
            )

    def effective_form_names(self) -> list[str]:
        """UI picker overrides or augments resolved form names."""
        ui = [n.strip() for n in (self.ui_form_types or []) if n and n.strip()]
        if ui:
            return ui
        return list(self.resolved.form_names)

    def tool_trace(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self.tool_results]

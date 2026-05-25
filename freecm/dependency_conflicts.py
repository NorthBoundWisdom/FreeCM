"""Dependency conflict diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    from .errors import FreeCMError
except ImportError:  # pragma: no cover - supports direct script execution.
    from errors import FreeCMError


@dataclass(frozen=True)
class DependencyConflictSide:
    source: str | None
    parent_dependency_name: str | None
    value: str

    def as_json_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "parentDependencyName": self.parent_dependency_name,
            "value": self.value,
        }


@dataclass(frozen=True)
class DependencyConflictDiagnostic:
    dependency_name: str
    field_name: str
    existing: DependencyConflictSide
    candidate: DependencyConflictSide

    @property
    def message(self) -> str:
        return (
            f"Dependency closure conflict for {self.dependency_name}: "
            f"{self.field_name} mismatch"
        )

    @property
    def suggested_actions(self) -> tuple[str, ...]:
        return (
            "Update the parent lock templates so this dependency resolves to one remote and commit.",
            "If both versions are intentional, assign distinct dependency names or ABI groups and update downstream locks in dependency order.",
        )

    def as_text(self) -> str:
        return (
            f"{self.message}\n"
            f"- existing: {self.existing.source or '<unknown>'} "
            f"({self.existing.parent_dependency_name or 'root'}) {self.existing.value!r}\n"
            f"- candidate: {self.candidate.source or '<unknown>'} "
            f"({self.candidate.parent_dependency_name or 'root'}) {self.candidate.value!r}\n"
            "Suggested actions:\n"
            + "\n".join(f"- {action}" for action in self.suggested_actions)
        )

    def as_json_dict(self) -> dict[str, Any]:
        return {
            "dependencyName": self.dependency_name,
            "fieldName": self.field_name,
            "message": self.message,
            "existing": self.existing.as_json_dict(),
            "candidate": self.candidate.as_json_dict(),
            "suggestedActions": list(self.suggested_actions),
        }


class DependencyConflictError(FreeCMError, ValueError):
    def __init__(self, diagnostic: DependencyConflictDiagnostic) -> None:
        super().__init__(diagnostic.as_text())
        self.diagnostic = diagnostic

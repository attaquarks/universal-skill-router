from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class DecisionState(str, Enum):
    USE_SKILL = "USE_SKILL"
    MULTI_SKILL = "MULTI_SKILL"
    AMBIGUOUS = "AMBIGUOUS"
    NO_MATCH = "NO_MATCH"


@dataclass(slots=True)
class SkillRecord:
    id: str
    name: str
    path: str
    root: str
    source_hash: str
    mtime_ns: int
    size: int
    description: str = ""
    domain: str = ""
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    related_skills: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    body_excerpt: str = ""
    is_orchestrator: bool = False
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SkillRecord":
        return cls(**{k: v for k, v in value.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Candidate:
    skill_id: str
    name: str
    score: float
    evidence: list[str]
    field_scores: dict[str, float]
    is_orchestrator: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RouteDecision:
    state: DecisionState
    query: str
    primary: Candidate | None
    supporting: list[Candidate]
    candidates: list[Candidate]
    confidence: float
    reason: str
    activation: list[dict[str, str]] = field(default_factory=list)
    chain: list[list[str]] = field(default_factory=list)
    project: str | None = None
    enforcement: str = "ADVISORY"
    latency_ms: float = 0.0
    semantic_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        return value


def under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False

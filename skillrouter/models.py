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
    # Applicability sentences lifted from the description. Optional and additive: an index written
    # before this field existed loads fine and the router derives it from the description on load.
    use_when: str = ""
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
    # Distinctive matched terms and phrase anchors; used by confidence gates, not by scoring.
    anchor_hits: int = 0
    # Rare matched terms found in a strong field (name/triggers/aliases/keywords) and
    # whether the match was anchored on the skill's own name. The evidence gate reads these;
    # scoring never does, so they cannot change ranking.
    strong_hits: int = 0
    name_signal: bool = False
    # How many distinct query terms the skill's own name claims. Two or more, or one rare one,
    # is a skill-name claim even when those words are common in English (api-design-reviewer).
    name_hits: int = 0

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
    # Additive v0.2 fields: query path triage, the gate that produced a NO_MATCH, and query diagnostics.
    path: str = "GENERAL"
    gate: str | None = None
    query_profile: dict[str, Any] = field(default_factory=dict)

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

from __future__ import annotations

import hashlib
import json
import math
import os
import shlex
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .config import LEARNED_PATH
from .indexer import load_records
from .models import Candidate, DecisionState, RouteDecision, SkillRecord, under_root
from .parser import tokenize

FIELD_WEIGHTS = {"name": 3.0, "triggers": 2.5, "aliases": 2.0, "keywords": 1.6, "description": 1.2, "body": 0.8, "domain": 1.4}
GENERIC = frozenset("build create change improve issue problem project app application api code system help make review design production better thinking now my".split())
SYNONYMS = {"slow": ("performance", "latency"), "failing": ("debug", "failure"), "bug": ("debug", "failure"),
    "pull": ("pr",), "request": ("pr",), "deploy": ("deployment", "release"), "schema": ("database",),
    "rag": ("retrieval", "embedding"), "retrieval": ("rag",), "websocket": ("realtime", "socket")}
PHRASE_SYNONYMS = {"pull request": "pr", "row level security": "rls", "real time": "realtime"}


class Router:
    def __init__(self, config: dict[str, Any], records: list[SkillRecord] | None = None, index_path: Path | None = None):
        self.config = config
        self.records = records if records is not None else (load_records(index_path) if index_path else load_records())
        self.by_id = {record.id: record for record in self.records}
        self.by_name: dict[str, list[SkillRecord]] = defaultdict(list)
        for record in self.records:
            self.by_name[record.name.lower()].append(record)
        self._fields: dict[str, dict[str, list[str]]] = {}
        for record in self.records:
            self._fields[record.id] = {
                "name": tokenize(record.name.replace("-", " ")),
                "triggers": tokenize(" ".join(record.triggers)),
                "aliases": tokenize(" ".join(record.aliases)),
                "keywords": record.keywords,
                "description": tokenize(record.description),
                "body": tokenize(record.body_excerpt),
                "domain": tokenize(record.domain),
            }
        self._idf = self._build_idf()
        self._learned = self._load_learned()

    def _build_idf(self) -> dict[str, float]:
        documents = [set(token for field in self._fields[record.id].values() for token in field) for record in self.records]
        counts = Counter(token for doc in documents for token in doc)
        size = max(1, len(documents))
        return {token: math.log((size + 1) / (count + 0.5)) + 1 for token, count in counts.items()}

    def _load_learned(self) -> dict[str, Any]:
        try:
            return json.loads(LEARNED_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _bm25(query: list[str], document: list[str], idf: dict[str, float]) -> float:
        if not document:
            return 0.0
        counts = Counter(document)
        # A per-field normalization keeps descriptions from swamping concise skills.
        denominator_base = 1.2 * (0.25 + 0.75 * min(len(document), 80) / 80)
        return sum(idf.get(term, 0.0) * (counts[term] * 2.2) / (counts[term] + denominator_base) for term in set(query) if term in counts)

    def _project_route(self, query: str) -> dict[str, Any] | None:
        normalized = query.lower()
        for project in self.config.get("projects", []):
            aliases = project.get("aliases", []) if isinstance(project, dict) else []
            if any(str(alias).lower() in normalized for alias in aliases):
                return project
        return None

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        direct = tokenize(query)
        expanded = [synonym for term in direct for synonym in SYNONYMS.get(term, ())]
        lowered = query.lower()
        expanded.extend(alias for phrase, alias in PHRASE_SYNONYMS.items() if phrase in lowered)
        return direct + expanded

    def _manual_chain(self, project: dict[str, Any] | None, query: str) -> list[list[str]]:
        if not project:
            return []
        lowered = query.lower()
        for route in project.get("routes", []):
            if any(str(trigger).lower() in lowered for trigger in route.get("when", [])):
                return [list(step) if isinstance(step, list) else [step] for step in route.get("chain", [])]
        return []

    def _score(self, query: str, record: SkillRecord, project: dict[str, Any] | None) -> Candidate:
        direct_terms = tokenize(query)
        terms = self._query_terms(query)
        fields = self._fields[record.id]
        field_scores = {field: self._bm25(terms, content, self._idf) * FIELD_WEIGHTS[field] for field, content in fields.items()}
        score = sum(field_scores.values())
        lowered = query.lower()
        name_phrase = record.name.lower().replace("-", " ")
        evidence: list[str] = []
        if name_phrase in lowered or record.name.lower() in lowered:
            score += 5.0
            evidence.append("exact skill-name phrase")
        exact_name_tokens = set(direct_terms) & set(fields["name"])
        distinctive_name_tokens = [token for token in exact_name_tokens if token not in GENERIC and self._idf.get(token, 0) >= 1.5]
        if distinctive_name_tokens:
            score += sum(self._idf.get(token, 1) * 10.0 for token in distinctive_name_tokens)
            evidence.append("name signal: " + ", ".join(distinctive_name_tokens[:3]))
        phrase_name_tokens = [alias for phrase, alias in PHRASE_SYNONYMS.items() if phrase in lowered and alias in fields["name"]]
        if phrase_name_tokens:
            score += sum(self._idf.get(token, 1) * 9.0 for token in phrase_name_tokens)
            evidence.append("canonical phrase signal: " + ", ".join(phrase_name_tokens))
        synonym_name_tokens = [synonym for term in direct_terms for synonym in SYNONYMS.get(term, ()) if synonym in fields["name"]]
        if synonym_name_tokens:
            score += sum(self._idf.get(token, 1) * 2.0 for token in set(synonym_name_tokens))
            evidence.append("synonym name signal: " + ", ".join(sorted(set(synonym_name_tokens))))
        for phrase in [*record.aliases, *record.triggers]:
            phrase = phrase.strip().lower()
            if len(phrase) > 3 and phrase in lowered:
                score += 2.4
                evidence.append(f"phrase: {phrase[:80]}")
        matched = sorted(set(terms) & set(fields["name"] + fields["triggers"] + fields["aliases"] + fields["keywords"]))
        distinctive = [term for term in matched if term not in GENERIC and self._idf.get(term, 0) >= 1.5]
        if distinctive:
            evidence.append("distinctive: " + ", ".join(distinctive[:4]))
        if project:
            preferred = {str(skill).lower() for skill in project.get("preferred_skills", [])}
            if record.name.lower() in preferred:
                score += 2.0
                evidence.append("project preference")
            if record.name.lower() in {str(skill).lower() for skill in project.get("orchestrators", [])}:
                score += 2.5
                evidence.append("project orchestrator")
        # Learning stores compact tokens only and can only make a modest, explainable boost.
        boosts = self._learned.get("token_skill_boosts", {})
        learned = sum(float(boosts.get(term, {}).get(record.name, 0)) for term in set(terms))
        if learned:
            score += min(1.5, learned)
            evidence.append("local learned signal")
        if record.is_orchestrator and self.config.get("routing", {}).get("prefer_orchestrators", True) and score > 0:
            # A small preference, never a selection without semantic evidence.
            score += 0.35
            evidence.append("declared/discovered orchestrator")
        return Candidate(record.id, record.name, score, evidence, field_scores, record.is_orchestrator)

    @staticmethod
    def _confidence(top: Candidate | None, second: Candidate | None) -> tuple[float, float]:
        if not top or top.score <= 0:
            return 0.0, 0.0
        # Saturating score + a relative margin prevents weak one-candidate certainty.
        strength = 1 - math.exp(-top.score / 9)
        margin = (top.score - second.score) / max(top.score, 0.001) if second else 1.0
        return round(max(0.0, min(0.99, strength * (0.6 + 0.4 * max(0, margin)))), 3), margin

    @staticmethod
    def _distinctive_signals(candidate: Candidate) -> set[str]:
        signals: set[str] = set()
        for evidence in candidate.evidence:
            if ":" in evidence and any(evidence.startswith(prefix) for prefix in ("name signal:", "synonym name signal:", "canonical phrase signal:")):
                signals.update(part.strip() for part in evidence.split(":", 1)[1].split(",") if part.strip())
        return signals

    def _semantic_tiebreak(self, query: str, candidates: list[Candidate]) -> list[Candidate] | None:
        semantic = self.config.get("semantic", {})
        command = semantic.get("command", "")
        if not semantic.get("enabled") or not command:
            return None
        # Explicit user configuration only. No skill text is executed; compact candidate excerpts are JSON stdin.
        payload = {"query": query, "candidates": [{"name": c.name, "id": c.skill_id, "excerpt": self.by_id[c.skill_id].body_excerpt[:3000]} for c in candidates[:5]]}
        try:
            process = subprocess.run(shlex.split(command, posix=os.name != "nt"), input=json.dumps(payload), shell=False, text=True, capture_output=True,
                                     timeout=float(semantic.get("timeout_seconds", 5)), check=False)
            result = json.loads(process.stdout)
            ordered = result.get("ordered_ids", []) if isinstance(result, dict) else []
            if not isinstance(ordered, list):
                return None
            rank = {value: index for index, value in enumerate(ordered)}
            return sorted(candidates, key=lambda candidate: rank.get(candidate.skill_id, len(rank)))
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            return None

    def route(self, query: str, semantic: bool = False) -> RouteDecision:
        started = time.perf_counter()
        project = self._project_route(query)
        chain = self._manual_chain(project, query)
        ranked = sorted((self._score(query, record, project) for record in self.records), key=lambda candidate: candidate.score, reverse=True)
        limit = int(self.config.get("routing", {}).get("max_candidates", 5))
        candidates = ranked[:limit]
        semantic_used = False
        top = ranked[0] if ranked else None
        top_signals = self._distinctive_signals(top) if top else set()
        independent = [candidate for candidate in ranked[1:] if self._distinctive_signals(candidate) - top_signals]
        second = independent[0] if independent else (ranked[1] if len(ranked) > 1 else None)
        confidence, margin = self._confidence(top, second)
        has_intent_signal = bool(top and (self._distinctive_signals(top) or any(item.startswith("distinctive:") for item in top.evidence)
                                          or any(item.startswith(("exact skill-name phrase", "phrase:")) for item in top.evidence)))
        if top and not has_intent_signal:
            # A high-scoring accidental overlap is not enough to select a skill.
            # Keep candidates visible, but require at least one meaningful intent signal.
            if top.score > 0:
                confidence = max(0.30, min(confidence, 0.59))
        required_margin = float(self.config.get("confidence", {}).get("margin", 0.12))
        if semantic and top and confidence < float(self.config.get("confidence", {}).get("use", 0.62)):
            reordered = self._semantic_tiebreak(query, candidates)
            if reordered:
                candidates, semantic_used = reordered, True
                top = candidates[0] if candidates else None
                second = candidates[1] if len(candidates) > 1 else None
                confidence, margin = self._confidence(top, second)
        thresholds = self.config.get("confidence", {})
        state = DecisionState.NO_MATCH
        supporting: list[Candidate] = []
        reason = "No indexed skill has enough distinctive evidence."
        if top and confidence >= float(thresholds.get("use", 0.62)) and margin >= required_margin:
            state = DecisionState.USE_SKILL
            reason = "High lexical evidence and a clear lead over alternatives."
            support_threshold = float(thresholds.get("support", 0.47))
            max_chain = int(self.config.get("routing", {}).get("max_chain_skills", 3))
            covered_signals = set(self._distinctive_signals(top))
            for candidate in ranked[1:]:
                relative = candidate.score / max(top.score, 0.001)
                new_signals = self._distinctive_signals(candidate) - covered_signals
                if relative >= support_threshold and new_signals and len(supporting) < max_chain - 1:
                    supporting.append(candidate)
                    covered_signals.update(new_signals)
            for candidate in supporting:
                if all(existing.skill_id != candidate.skill_id for existing in candidates):
                    if len(candidates) >= limit: candidates[-1] = candidate
                    else: candidates.append(candidate)
            if supporting:
                state = DecisionState.MULTI_SKILL
                reason = "Primary evidence plus an independently relevant supporting skill."
        elif top and confidence >= float(thresholds.get("no_match", 0.28)):
            state = DecisionState.AMBIGUOUS
            reason = "Candidates overlap or evidence is too weak for safe automatic selection."
        if chain:
            selected_names = [name for step in chain for name in step if str(name).lower() in self.by_name]
            if selected_names:
                state, reason = DecisionState.MULTI_SKILL, "A project-specific saved chain matched the task."
                selected = [next((candidate for candidate in ranked if candidate.name.lower() == str(name).lower()), None) for name in selected_names]
                selected = [candidate for candidate in selected if candidate]
                if selected:
                    top, supporting, confidence = selected[0], selected[1:], 0.98
                    candidates = selected[:limit]
        activation = []
        for candidate in ([top] if top and state in (DecisionState.USE_SKILL, DecisionState.MULTI_SKILL) else []) + supporting:
            record = self.by_id[candidate.skill_id]
            resolved = Path(record.path).resolve()
            allowed_roots = [Path(root) for root in self.config.get("roots", []) if Path(root).exists()] or [Path(record.root)]
            if any(under_root(resolved, root) for root in allowed_roots):
                activation.append({"skill": record.name, "path": str(resolved), "action": "load_instructions"})
        chosen = top if state in (DecisionState.USE_SKILL, DecisionState.MULTI_SKILL) else None
        return RouteDecision(state, query, chosen, supporting, candidates, confidence,
            reason, activation, chain, project.get("name") if project else None, self.config.get("enforcement", {}).get("mode", "ADVISORY"),
            round((time.perf_counter() - started) * 1000, 2), semantic_used)

    def handoff(self, decision: RouteDecision) -> dict[str, Any]:
        return {"version": 1, "route": {"state": decision.state.value, "primary": decision.primary.name if decision.primary else None,
            "supporting": [candidate.name for candidate in decision.supporting], "activation": decision.activation, "enforcement": decision.enforcement,
            "gates": [], "project": decision.project}}

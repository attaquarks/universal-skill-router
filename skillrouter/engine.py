from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .config import LEARNED_PATH
from .indexer import load_records
from .models import Candidate, DecisionState, RouteDecision, SkillRecord, under_root
from .parser import extract_use_when, tokenize


# Hyphenated compounds are one identifier to a user and several words to the index: leaving
# "multi-tenant" whole means it can never meet the name part "tenant", even though a user typing
# "tenant" would match. Split query compounds into their parts, keeping the compound too.
_QUERY_SPLIT_RE = re.compile(r"[-_/]+")


def _query_tokens(query: str) -> list[str]:
    """Tokens for a query: each token, the parts of any hyphenated compound, and trivial inflections.

    "multi-tenant SSO" -> ["multi-tenant", "multi", "tenant", "sso"]. Order and dedup are preserved so
    the compound keeps its verbatim match against keywords and descriptions.

    Inflections are folded the same way the out-of-vocabulary guard already folds them, so the matcher
    and the guard agree on what counts as the same word: "vendors" must reach a skill whose own name
    says "vendor", and "SLOs" must reach "SLO". The folded forms are only ever added -- never
    substituted -- so an exact match still outranks an approximate one.
    """
    tokens = tokenize(query)
    for token in list(tokens):
        if _QUERY_SPLIT_RE.search(token):
            tokens.extend(tokenize(token.replace("-", " ").replace("_", " ").replace("/", " ")))
    folded: list[str] = []
    for token in tokens:
        for variant in _INFLECTIONS(token):
            if variant != token and variant not in tokens:
                folded.append(variant)
    return list(dict.fromkeys(tokens + folded))


def _INFLECTIONS(term: str) -> set[str]:
    """Trivial inflections of a term, mirroring Router._variants so query and guard stay in step."""
    variants = {term}
    for suffix, replacement in (("ies", "y"), ("es", ""), ("s", ""), ("ing", ""), ("ing", "e"), ("ed", ""), ("ed", "e")):
        if term.endswith(suffix) and len(term) - len(suffix) >= 3:
            variants.add(term[: len(term) - len(suffix)] + replacement)
    return variants

# Fields whose vocabulary is specific to the skill, in specificity order. Description/body are
# deliberately excluded: a rare word merely mentioned in prose is not evidence of a skill.
STRONG_FIELDS = ("name", "aliases", "triggers", "keywords")
FIELD_WEIGHTS = {"name": 3.0, "usewhen": 2.2, "triggers": 2.5, "aliases": 2.0, "keywords": 1.6, "description": 1.2, "body": 0.8, "domain": 1.4}
GENERIC = frozenset("build create change improve issue problem project app application api code system help make review design production better thinking now my".split())
# Terms that look distinctive but appear across many skills (e.g. "escalat" in manager skills); never count as a domain anchor.
ANCHOR_STOP = frozenset("""escalat escalate escalates escalation escalations phase phases tier tiers step steps stage stages doc docs
pattern patterns skill skills trigger triggers router routing route routes example examples note notes option options task tasks
team teams time times mode modes case cases item items list lists set sets call calls run runs work works user users file files
level levels area areas name names value values type types data start end new use used using required require support supported
provide provides include includes general overall
# Manner and time adverbs: they shape a request but never identify a domain, and common ones sit just
# inside the rarity bound ("fast" in 13% of skills), where they would otherwise pass the evidence gate.
fast quick quickly slower fastest soon later again today tomorrow yesterday immediately always never often sometimes
better best worse good great nice proper correctly properly simply easily hard harder""".split())
SYNONYMS = {"slow": ("performance", "latency"), "failing": ("debug", "failure"), "bug": ("debug", "failure"),
    "pull": ("pr",), "request": ("pr",), "deploy": ("deployment", "release"), "schema": ("database",),
    "rag": ("retrieval", "embedding"), "retrieval": ("rag",), "websocket": ("realtime", "socket"),
    # Failure vocabulary. A problem statement is usually phrased in symptoms ("it crashes", "rows are
    # incorrect", "we have a regression") while the skills that resolve it are written in causes
    # ("debugging", "error recovery", "systematic debugging"). These entries bridge that gap, which is
    # why the BROKEN cases were missing: the right skills were not retrieved at all, so no amount of
    # ranking or evidence work could reach them.
    #
    # Each entry maps a symptom onto problem/triage vocabulary. They deliberately do NOT map onto domain
    # nouns: "stuck" -> ("blocked", "queue", "hang") dragged inbox and message-board skills into a
    # debugging query and cost a wrong-skill case, so only the triage half is kept.
    "crashes": ("crash", "bug", "failure"), "crashing": ("crash", "bug"),
    "regression": ("bug", "failure"), "incorrect": ("wrong", "invalid", "bug"),
    "corruption": ("incorrect", "integrity", "data"), "corrupt": ("incorrect", "integrity", "data"),
    "leak": ("memory", "leaking"), "leaks": ("memory", "leaking"),
    "stuck": ("blocked", "hang"), "exhausted": ("failure", "timeout"),
    "cannot": ("failure", "broken")}
PHRASE_SYNONYMS = {"pull request": "pr", "row level security": "rls", "real time": "realtime"}
# Evidence-gate constants. Rareness is read from document frequency in this corpus, not a fixed
# IDF cutoff, so the gate travels to a corpus of any size.
RARE_DF_RATIO = 0.14
VERY_RARE_DF_RATIO = 0.03
# A query this short cannot carry enough evidence to select a skill.
SHORT_QUERY_TOKENS = 2
# The corpus-context guard needs a corpus large enough for "this word is absent" to mean "the domain
# is uncovered". Below this many indexed skills the vocabulary is too sparse for absence to be
# informative, and the evidence gate alone decides. Fixtures and tiny installs are unaffected.
MIN_CORPUS_FOR_OOV = 30
MATCH_EVIDENCE = "match: "
# Lexical path triage. Ordered BROKEN -> BUILD -> OPERATE; the first signal class wins, ties break by latest position.
PATH_LEXICON: dict[str, tuple[str, ...]] = {
    "BROKEN": ("broken", "failing", "fails", "failure", "error", "errors", "bug", "bugs", "crash", "crashes", "crashing",
        "down", "outage", "regression", "regressed", "fix", "fixing", "investigate", "debug", "debugging", "stuck",
        "misbehaving", "incorrect", "wrong", "unexpected", "broke", "not working", "doesn't work", "does not work", "hang", "hangs"),
    "BUILD": ("build", "create", "creating", "implement", "implementing", "add", "adding", "write", "writing", "develop",
        "developing", "design", "designing", "make", "making", "construct", "scaffold", "prototype", "generate", "draft",
        "set up", "prepare", "preparing", "plan", "planning", "drafting", "provision", "bootstrap"),
    "OPERATE": ("review", "reviewing", "optimize", "optimizing", "improve", "improving", "refactor", "refactoring",
        "deploy", "deploying", "release", "audit", "auditing", "check", "checking", "analyze", "analyzing", "harden",
        "maintain", "monitor", "cleanup", "clean", "migrate", "upgrade", "edit", "editing", "reduce", "reducing",
        "size", "scoping", "tune", "consolidate", "simplify"),
}
_WORD_BOUNDARY_CACHE: dict[str, re.Pattern[str]] = {}
_PATH_RANK = {"GENERAL": 0, "OPERATE": 1, "BUILD": 2, "BROKEN": 3}  # most-specific-wins tie-break
# Work-intent vocabulary. A prompt with none of these and a question shape is conversational, not a task.
# Kept separate from PATH_LEXICON so triage and the out-of-domain guard can be tuned independently.
WORK_INTENT_EXTRA: dict[str, tuple[str, ...]] = {
    "BUILD": ("plan", "planning", "instrument", "instrumenting", "rewrite", "restructure", "port", "provision"),
    "OPERATE": ("research", "researching", "summarize", "summarizing", "tune", "tuning", "profile", "profiling",
        "validate", "validating", "measure", "measuring", "document", "documenting", "consolidate"),
    "BROKEN": ("broken", "regression", "outage", "deadlock", "leak", "timeout", "timeouts", "flaky", "hang", "hangs",
        "stuck", "misconfigured", "exhausted", "slow", "degraded",
        # Bare forms matter: "our tests fail" and "the build breaks" are the ordinary phrasings.
        "fail", "fails", "break", "breaks", "crashed"),
}

# The operation vocabulary used for triage: both lexicons, unioned per path. Merging the dicts with `**`
# would replace the main lexicon's term list instead of extending it.
PATH_VOCABULARY: dict[str, tuple[str, ...]] = {
    path: tuple(dict.fromkeys(PATH_LEXICON.get(path, ()) + WORK_INTENT_EXTRA.get(path, ())))
    for path in ("BROKEN", "BUILD", "OPERATE")}
# Operations are not domains. "refactor", "review", "optimize", "deploy" say what to do with a subject,
# never what the subject is, so they can never serve as the distinctive evidence that identifies a skill.
# Deliberately NOT built from SYNONYMS: its keys are query *subjects* ("rag" -> retrieval, "schema" ->
# database), and excluding those would discard real evidence. PATH_LEXICON and WORK_INTENT_EXTRA
# already cover the operation words that also appear as synonym keys (deploy, bug, failing, slow).
OPERATION_STOP = frozenset({term for terms in PATH_LEXICON.values() for term in terms}
                           | {term for terms in WORK_INTENT_EXTRA.values() for term in terms})
# Path-aware explanations. Each path says what the router thinks the task is, so a misroute is
# diagnosable from the reason string alone.
PATH_REASON = {
    "BROKEN": "A problem statement with evidence pointing at a diagnostics skill.",
    "BUILD": "Work that creates or constructs something, with construction evidence.",
    "OPERATE": "Work that reviews, audits or improves something already built.",
    "GENERAL": "High lexical evidence and a clear lead over alternatives.",
}
PATH_MEANING = {
    "BROKEN": "something is wrong, failing or not behaving as expected",
    "BUILD": "something new is being created",
    "OPERATE": "something existing is being reviewed, audited or improved",
    "GENERAL": "no operation verb identified; triage did not narrow the task",
}
QUESTION_MARKERS = frozenset("what when where which who whom whose why how is are was were does do did can could should would will".split())
# Terra incognita: questions about the world rather than about the user's own work.
WORLD_NOUNS = frozenset("""capital country president weather book books joke jokes recipe recipes holiday vacation
flight flights dentist appointment appointment gold price score match game movie film song music football cricket
history population moons jupiter planet distance translate meaning define""".split())


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
            # Always derived, never read back: the field is a pure function of the description, so
            # deriving it means an index written by an earlier version picks up the current extraction
            # with no rebuild, and every install scores the same text for the same SKILL.md.
            use_when = extract_use_when(record.description)
            self._fields[record.id] = {
                "name": tokenize(record.name.replace("-", " ")),
                "usewhen": tokenize(use_when),
                "triggers": tokenize(" ".join(record.triggers)),
                "aliases": tokenize(" ".join(record.aliases)),
                "keywords": record.keywords,
                "description": tokenize(record.description),
                "body": tokenize(record.body_excerpt),
                "domain": tokenize(record.domain),
            }
        self._idf = self._build_idf()
        # Precompute per-field term counts and the BM25 document constants once: they never change per query.
        self._term_counts = {record.id: {field: Counter(content) for field, content in self._fields[record.id].items()} for record in self.records}
        self._bm25_base = {record.id: {field: 1.2 * (0.25 + 0.75 * min(len(content), 80) / 80) for field, content in self._fields[record.id].items()} for record in self.records}
        self._learned = self._load_learned()

    def _build_idf(self) -> dict[str, float]:
        documents = [set(token for field in self._fields[record.id].values() for token in field) for record in self.records]
        counts = Counter(token for doc in documents for token in doc)
        size = max(1, len(documents))
        # Document frequency is kept for evidence gates and diagnostics (name-vs-description weighting).
        self._df = dict(counts)
        self._doc_count = size
        return {token: math.log((size + 1) / (count + 0.5)) + 1 for token, count in counts.items()}

    def _load_learned(self) -> dict[str, Any]:
        try:
            return json.loads(LEARNED_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _bm25(query: list[str], counts: Counter[str], base: float, idf: dict[str, float]) -> float:
        if not counts:
            return 0.0
        # A per-field normalization keeps descriptions from swamping concise skills.
        return sum(idf.get(term, 0.0) * (counts[term] * 2.2) / (counts[term] + base) for term in set(query) if term in counts)

    def _project_route(self, query: str) -> dict[str, Any] | None:
        normalized = query.lower()
        for project in self.config.get("projects", []):
            aliases = project.get("aliases", []) if isinstance(project, dict) else []
            if any(str(alias).lower() in normalized for alias in aliases):
                return project
        return None

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        direct = _query_tokens(query)
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

    def _score(self, prepared: tuple[str, list[str], list[str]], record: SkillRecord, project: dict[str, Any] | None) -> Candidate:
        lowered, direct_terms, terms = prepared
        fields = self._fields[record.id]
        counts = self._term_counts[record.id]
        bases = self._bm25_base[record.id]
        field_scores = {field: self._bm25(terms, counts[field], bases[field], self._idf) * FIELD_WEIGHTS[field] for field in fields}
        score = sum(field_scores.values())
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
        phrase_hits = 0
        for phrase in [*record.aliases, *record.triggers]:
            phrase = phrase.strip().lower()
            if len(phrase) > 3 and phrase in lowered:
                score += 2.4
                phrase_hits += 1
                evidence.append(f"phrase: {phrase[:80]}")
        # A term claimed in the skill's own identity (name, aliases, triggers) counts as evidence even
        # when it is an operation word: a skill named "copy-editing" or "review-agent" genuinely claims
        # that operation. Keywords are excluded from that exemption because they are derived from prose,
        # which is where incidental operation words leak in.
        identity = set(fields["name"]) | set(fields["aliases"]) | set(fields["triggers"])
        matched = sorted(set(terms) & set(fields["name"] + fields["triggers"] + fields["aliases"] + fields["keywords"]))
        distinctive = [term for term in matched if self._anchor(term) or (term in identity and term not in GENERIC)]
        if distinctive:
            evidence.append("distinctive: " + ", ".join(distinctive[:4]))
            # Diagnostic only: which strong field claimed each term. Scoring never reads this.
            evidence.append(MATCH_EVIDENCE + " ".join(f"{term}:{self._strongest_field(record.id, term)}" for term in distinctive[:6]))
        name_signal = bool(distinctive_name_tokens or phrase_name_tokens or synonym_name_tokens
                           or any(term in fields["name"] for term in distinctive))
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
        strong_hits = sum(1 for term in distinctive if self._counts_as_strong(record.id, term))
        # Name claims are counted separately from rareness: a skill legitimately named after common
        # words ("api-design-reviewer") is still the right skill for "review our API design".
        name_hits = (len({term for term in direct_terms if term in fields["name"]})
                     + len(set(phrase_name_tokens) | set(synonym_name_tokens)))
        return Candidate(record.id, record.name, score, evidence, field_scores, record.is_orchestrator,
                         anchor_hits=len(distinctive) + phrase_hits, strong_hits=strong_hits, name_signal=name_signal,
                         name_hits=name_hits)

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

    def _rare(self, term: str) -> bool:
        """Rare in this corpus, by document frequency. A term absent from the vocabulary is not rare, it is unknown."""
        df = self._df.get(term, 0)
        return 0 < df <= max(1, math.ceil(RARE_DF_RATIO * self._doc_count))

    def _very_rare(self, term: str) -> bool:
        df = self._df.get(term, 0)
        return 0 < df <= max(1, math.ceil(VERY_RARE_DF_RATIO * self._doc_count))

    def _anchor(self, term: str) -> bool:
        """A term only counts as domain evidence when it is rare across the corpus and not generic filler."""
        return term not in GENERIC and term not in ANCHOR_STOP and term not in OPERATION_STOP and self._rare(term)

    def _counts_as_strong(self, skill_id: str, term: str) -> bool:
        """Is this term strong evidence for this skill?

        Name, aliases and triggers are identity: the skill declares the term about itself, so any hit
        there identifies it. Keywords are derived from prose -- parse_skill builds them from
        name+description+domain+tags+headings, and 82% of keyword tokens come from the description
        alone -- so a keyword hit counts only when the term is *very* rare in this corpus. "owasp" and
        "accessibility" identify a skill; "module" and "remove" are ordinary prose that dozens of
        skills happen to mention, and on those alone the router should ask rather than guess.
        """
        field = self._strongest_field(skill_id, term)
        if field in ("name", "aliases", "triggers"):
            return True
        if field == "keywords":
            return self._very_rare(term)
        return False

    def _strongest_field(self, skill_id: str, term: str) -> str:
        """Which strong field claims this term, most specific first."""
        fields = self._fields[skill_id]
        for field in STRONG_FIELDS:
            if term in fields[field]:
                return field
        return "description"

    @staticmethod
    def _variants(term: str) -> set[str]:
        """Trivial inflections of a term. "breaks" must not read as unknown because the corpus says
        "break"; plural and tense drift are the common case, not the exception."""
        variants = {term}
        for suffix, replacement in (("ies", "y"), ("es", ""), ("s", ""), ("ing", ""), ("ing", "e"), ("ed", ""), ("ed", "e")):
            if term.endswith(suffix) and len(term) - len(suffix) >= 3:
                variants.add(term[: len(term) - len(suffix)] + replacement)
        return variants

    def _known(self, term: str) -> bool:
        """True when the corpus contains this term or a trivial inflection of it."""
        return any(self._df.get(candidate, 0) > 0 for candidate in self._variants(term))

    def _oov_ratio(self, tokens: list[str]) -> float:
        """Share of query terms that appear in no indexed skill, allowing for inflections."""
        return sum(1 for term in tokens if not self._known(term)) / len(tokens) if tokens else 0.0

    @staticmethod
    def _classify_path(query: str) -> str:
        """Cheap lexical triage: BROKEN / BUILD / OPERATE / GENERAL. No model, no extra scan.

        Rules, in order:
          1. Whole-word matching only. Substring matching read "zero-downtime" as a BROKEN "down".
          2. A problem signal anywhere makes the prompt BROKEN: an error is decisive even when the
             sentence also asks for something to be built.
          3. Otherwise the *earliest* signal in the sentence wins, because the leading verb carries the
             intent ("review the design" is OPERATE, not BUILD). Ties break by specificity.
        """
        text = query.lower()
        earliest: dict[str, int] = {}
        for path, terms in PATH_VOCABULARY.items():
            for term in terms:
                match = _WORD_BOUNDARY_CACHE.get(term)
                if match is None:
                    match = _WORD_BOUNDARY_CACHE[term] = re.compile(r"\b" + re.escape(term) + r"\b")
                found = match.search(text)
                if found and (path not in earliest or found.start() < earliest[path]):
                    earliest[path] = found.start()
        if not earliest:
            return "GENERAL"
        if "BROKEN" in earliest:
            return "BROKEN"
        return min(earliest, key=lambda path: (earliest[path], -_PATH_RANK[path]))

    def _path_thresholds(self) -> dict[str, dict[str, float]]:
        configured = self.config.get("confidence", {}).get("paths", {})
        return configured if isinstance(configured, dict) else {}

    def _work_intent(self, query: str, tokens: list[str]) -> bool:
        """Does this prompt ask for work, or is it a question/statement about something else?
        Lexical only: a work verb anywhere in the prompt, or a problem noun, counts as intent."""
        lowered = " " + query.lower() + " "
        vocabulary = {**PATH_LEXICON, **WORK_INTENT_EXTRA}
        for terms in vocabulary.values():
            for term in terms:
                if term in lowered:
                    return True
        if any(key in lowered for key in SYNONYMS):
            return True
        return False

    def _out_of_domain(self, query: str, tokens: list[str]) -> str | None:
        """Questions about the world, and prompts with no work verb, are not skill tasks."""
        if self._work_intent(query, tokens):
            return None
        lowered = " " + query.lower() + " "
        interrogative = lowered.strip().split(" ", 1)[0] in QUESTION_MARKERS or "?" in query
        if interrogative or any(term in WORLD_NOUNS for term in tokens):
            return "query asks about the world rather than a skill task"
        return None

    def _evidence_gate(self, query: str, tokens: list[str], top: Candidate | None) -> tuple[str, str] | None:
        """P0 out-of-domain guard. Returns (verdict, reason) where verdict is "no_match" for input that is
        not a skill task and "ambiguous" for plausible work that carries no identifiable skill.

        Every check is lexical and uses document frequency already computed for the index, so the gate
        adds no per-record work and no dependency. Order matters: cheap whole-query checks first."""
        meaningful = [term for term in tokens if term not in GENERIC and term not in ANCHOR_STOP]
        if tokens and not meaningful:
            # "help with my project" style input: nothing wrong with it, but no skill is identifiable.
            return "ambiguous", "query uses generic task vocabulary only"
        if len(tokens) < SHORT_QUERY_TOKENS:
            return "no_match", "query is too short to identify a skill task"
        if self._doc_count >= MIN_CORPUS_FOR_OOV and len(meaningful) >= 2 and self._oov_ratio(meaningful) >= 0.5:
            # Corpus-context guard: at least half the question is about something no installed skill
            # covers ("book me a dentist appointment", "what is the capital of Australia").
            return "no_match", "most query terms appear in no indexed skill"
        if not top or top.score <= 0:
            return "no_match", "no indexed skill matched any query term"
        matched = self._matched_terms(top)
        corroborated = [term for term in matched if self._strongest_field(top.skill_id, term) != "name"]
        if top.name_signal and not corroborated and len(matched) <= 1 and not any(self._rare(term) for term in matched):
            # Sharing one common noun with a skill name is a coincidence, not a match: "build a test
            # suite" must not select api-test-suite-builder. A single *rare* shared term is different —
            # "owasp" or "testflight" is the skill's own distinctive claim, and stands. This is a real
            # task with no identifiable skill, so ask rather than claim the prompt was out of domain.
            return "ambiguous", "only a partial skill name matched, with no other evidence"
        # A skill-name claim is evidence in its own right: the name is the skill's own statement of what
        # it is, so two claimed terms, or one rare claimed term, is enough even for common words.
        name_claim = (top.name_hits >= 2 or any(self._rare(term) for term in matched
            if self._strongest_field(top.skill_id, term) == "name")
            or any(item.startswith("exact skill-name phrase") for item in top.evidence))
        if name_claim:
            return None
        # Otherwise the winner needs at least one rare, non-generic term in a strong field. A rare word
        # that only appears in a description never counts, which is what stops "flow" picking ux-flow and
        # "holiday" picking a seo skill. Measured on the local 117-case suite: requiring a single strong
        # hit selects 6pp more positive cases than requiring two, with no change in no-match accuracy
        # and no change in wrong-skill rate.
        if top.strong_hits < 1:
            return "ambiguous", "no distinctive evidence from a skill's name, aliases, triggers or keywords"
        return None

    def _matched_terms(self, candidate: Candidate) -> list[str]:
        for evidence in candidate.evidence:
            if evidence.startswith(MATCH_EVIDENCE):
                return [part.split(":", 1)[0] for part in evidence[len(MATCH_EVIDENCE):].split() if part]
        return []

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
        # Query profiling is one extra tokenize pass over a short string; the per-record loop stays the only O(n) work.
        tokens = tokenize(query)
        # Query preparation is done exactly once per route, not once per scored record.
        prepared = (query.lower(), tokens, self._query_terms(query))
        gate = self._no_match_scope(query, tokens)
        path = self._classify_path(query) if self.config.get("routing", {}).get("triage", True) else "GENERAL"
        project = self._project_route(query) if gate is None else None
        chain = self._manual_chain(project, query)
        ranked = sorted((self._score(prepared, record, project) for record in self.records), key=lambda candidate: candidate.score, reverse=True)
        limit = int(self.config.get("routing", {}).get("max_candidates", 5))
        candidates = ranked[:limit]
        semantic_used = False
        top = ranked[0] if ranked else None
        gate = gate or self._evidence_gate(query, tokens, top)
        gate_verdict, gate_reason = gate if gate else (None, None)
        top_signals = self._distinctive_signals(top) if top else set()
        independent = [candidate for candidate in ranked[1:] if self._distinctive_signals(candidate) - top_signals]
        second = independent[0] if independent else (ranked[1] if len(ranked) > 1 else None)
        confidence, margin = self._confidence(top, second)
        thresholds = self.config.get("confidence", {})
        base_use = float(thresholds.get("use", 0.62))
        use_threshold = base_use
        for key, value in self._path_thresholds().get(path, {}).items():
            if key == "use" and isinstance(value, (int, float)):
                use_threshold = float(value)
        # A query subtype may only raise the bar, never lower it, and never over strong name-level evidence:
        # a clear skill-name or alias hit is trustworthy regardless of how the task is phrased.
        use_threshold = max(base_use, min(0.9, use_threshold))
        # "Names the skill" covers both a distinctive name term and an exact skill-name phrase. A
        # hyphenated name such as "performance-profiler" matches the query as one phrase while its
        # individual tokens ("performance", "profiler") do not intersect, so both cases must count.
        name_anchored = bool(top) and (top.name_signal
            or any(item.startswith("exact skill-name phrase") for item in top.evidence))
        if name_anchored:
            use_threshold = base_use
        required_margin = float(thresholds.get("margin", 0.12))
        if semantic and not gate_verdict and top and confidence < use_threshold:
            reordered = self._semantic_tiebreak(query, candidates)
            if reordered:
                candidates, semantic_used = reordered, True
                top = candidates[0] if candidates else None
                second = candidates[1] if len(candidates) > 1 else None
                confidence, margin = self._confidence(top, second)
        state = DecisionState.NO_MATCH
        supporting: list[Candidate] = []
        reason = "No indexed skill has enough distinctive evidence."
        if gate_verdict == "ambiguous":
            # The query is plausible work but nothing distinctive points at a specific skill: ask, never guess.
            state = DecisionState.AMBIGUOUS
            reason = f"No specific skill identified: {gate_reason}."
            if path != "GENERAL":
                reason += f" Query triaged as {path} work."
        elif gate_verdict == "no_match":
            # Out-of-domain, gibberish, stopword-only and evidence-free queries stop here.
            reason = f"No skill task detected: {gate_reason}."
        elif top and confidence >= use_threshold and margin >= required_margin:
            state = DecisionState.USE_SKILL
            reason = PATH_REASON.get(path, PATH_REASON["GENERAL"])
            if use_threshold != base_use:
                reason += f" (triaged {path}; raised threshold {use_threshold:.2f})."
            elif path != "GENERAL":
                reason += f" (triaged {path}; base threshold {base_use:.2f}, name evidence present)."
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
            if path != "GENERAL":
                reason += f" Query triaged as {path} ({PATH_MEANING[path]}) but no skill dominates."
        if chain:
            selected_names = [name for step in chain for name in step if str(name).lower() in self.by_name]
            if selected_names:
                state, reason, gate_verdict = DecisionState.MULTI_SKILL, "A project-specific saved chain matched the task.", None
                selected = [next((candidate for candidate in ranked if candidate.name.lower() == str(name).lower()), None) for name in selected_names]
                selected = [candidate for candidate in selected if candidate]
                if selected:
                    top, supporting, confidence = selected[0], selected[1:], 0.98
                    candidates = selected[:limit]
        if gate_verdict == "no_match":
            top, supporting = None, []
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
            round((time.perf_counter() - started) * 1000, 2), semantic_used, path, gate_reason)

    def _no_match_scope(self, query: str, tokens: list[str]) -> tuple[str, str] | None:
        """Whole-query guards that need no scoring: empty, gibberish, stopword-only or character-sparse input."""
        if not tokens:
            return ("no_match", "empty query") if not query.strip() else ("no_match", "query contains no routable terms")
        if len(tokens) >= 3:
            if sum(len(token) for token in tokens) / len(tokens) < 3.0:
                return "no_match", "query is too short or fragmented to route"
            numeric = sum(token.isdigit() for token in tokens)
            if numeric / len(tokens) >= 0.5:
                return "no_match", "query is numeric rather than a skill task"
        out_of_domain = self._out_of_domain(query, tokens)
        if out_of_domain:
            return "no_match", out_of_domain
        return None

    def handoff(self, decision: RouteDecision) -> dict[str, Any]:
        return {"version": 1, "route": {"state": decision.state.value, "primary": decision.primary.name if decision.primary else None,
            "supporting": [candidate.name for candidate in decision.supporting], "activation": decision.activation, "enforcement": decision.enforcement,
            "gates": [], "project": decision.project}}

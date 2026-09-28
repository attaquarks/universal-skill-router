"""Tests for BROKEN-scoped symptom→triage expansion.

A failure is reported by symptom ("the app crashes", "we have a leak") while the skills that resolve it
are NAMED with triage vocabulary (systematic-debugging, debugging-and-error-recovery, incident-commander).
The topic noun in such a query ("memory", "app", "pool", "queue") is frequently a *name* token of an
unrelated skill, so it scores the IDF×10 name boost and buries the resolver.

The fix is contextual, and these tests are mostly about the context: the mapping must apply when the
query is a problem statement and must NOT apply when the same words describe a topic to build or operate.
A global mapping would make "memory" mean "failure" in "design a memory pool", which is the same
topic-domination bug in reverse.

Fixtures deliberately contain the trap: skills named after the topic words, plus triage-named skills, plus
a vocabulary floor so the corpus-context guard does not fire for unrelated reasons.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from skillrouter.config import DEFAULT_CONFIG
from skillrouter.engine import Router, SYMPTOM_TRIAGE, SYNONYMS
from skillrouter.indexer import Indexer, load_records

CASES_PATH = Path(__file__).resolve().parents[1] / "eval" / "claude_skills_cases.json"

TRIAGE_TERMS = {term for synonyms in SYMPTOM_TRIAGE.values() for term in synonyms}
# Terms the scoped table contributes beyond the global table. "failure" arrives from both, so only these
# can prove the BROKEN gate fired; asserting on the whole set would test the global table by accident.
GLOBAL_TERMS = {term for synonyms in SYNONYMS.values() for term in synonyms}
SCOPED_ONLY = TRIAGE_TERMS - GLOBAL_TERMS

# Topic-named skills (the trap) and triage-named skills (the resolver), plus a floor.
FIXTURES = {
    "memory-engineering": "Design systems for agent memory and long-term recall.",
    "memory-status": "Report the health and line counts of stored memory.",
    "memory-discipline": "Keep memory usage tidy across a session.",
    "database-designer": "Design database schemas, indexes and migrations.",
    "sql-database-assistant": "Write and optimise SQL against a database.",
    "inbox-triage": "Sort an overflowing message inbox by priority.",
    "systematic-debugging": "Use when encountering any bug, test failure, or unexpected behavior.",
    "debugging-and-error-recovery": "Use when tests fail, builds break, or behaviour does not match.",
    "incident-commander": "Run incident response from detection through triage and resolution.",
    # vocabulary floor
    "code-reviewer": "Use when reviewing code and modules before a merge.",
    "changelog-generator": "Use when writing a changelog for a release.",
    "doc-writer": "Use when documenting a service or a module.",
    "merge-helper": "Use when merging a branch into the main line.",
    "style-guide": "Use when checking formatting and naming conventions.",
    "release-planner": "Use when preparing a release document.",
    "backend-reviewer": "Use when reviewing an endpoint in a service.",
    "coverage-planner": "Use when planning coverage for a module.",
    "cleanup-planner": "Use when planning a restructure of a module.",
    "api-designer": "Use when designing an interface contract.",
}

SYMPTOM_QUERIES = {
    "memory-leak": "Debug a memory leak in the worker process.",
    "pool-exhausted": "The database connection pool is exhausted and customers are down.",
    "app-crashes": "The app crashes whenever I open the settings screen.",
    "stuck-queue": "Messages are stuck in the queue and nothing is being processed.",
}

TOPIC_QUERIES = {
    "memory-pool-build": "Design a memory pool for the queue.",
    "connection-pool-build": "Design a multi-tenant connection pool for the database.",
    "worker-queue-build": "Create a job queue with a worker pool.",
    "pool-review": "Review our connection pool configuration.",
    "memory-optimize": "Optimize the memory usage of the worker.",
    "queue-audit": "Audit our queue retention policy.",
}


class ExpandedTermsTests(unittest.TestCase):
    """The gate itself, without a corpus."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = Path(tempfile.mkdtemp(prefix="skillrouter-gate-"))
        root = cls.temp / "skills"
        for name, description in FIXTURES.items():
            folder = root / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {description}\ntags:\n  - engineering\n---\n# {name}\n",
                encoding="utf-8")
        cls.router = Router(json.loads(json.dumps(DEFAULT_CONFIG)) | {"roots": [str(root)]},
                            records=None, index_path=None)
        # Build through the indexer so records carry their real parsed fields.
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["roots"] = [str(root)]
        index = cls.temp / "index.json"
        Indexer([root], config, index).build()
        cls.router = Router(config, load_records(index))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp, ignore_errors=True)

    def test_symptom_queries_are_classified_broken(self) -> None:
        for name, query in SYMPTOM_QUERIES.items():
            with self.subTest(case=name):
                self.assertEqual(self.router._classify_path(query), "BROKEN")

    def test_topic_queries_are_not_broken(self) -> None:
        for name, query in TOPIC_QUERIES.items():
            with self.subTest(case=name):
                self.assertNotEqual(self.router._classify_path(query), "BROKEN")

    def test_broken_path_adds_triage_terms(self) -> None:
        for name, query in SYMPTOM_QUERIES.items():
            with self.subTest(case=name):
                terms = set(self.router._query_terms(query, "BROKEN"))
                self.assertTrue(terms & SCOPED_ONLY,
                                f"no scoped triage term added for {name}: {sorted(terms)}")
                self.assertTrue(terms & TRIAGE_TERMS)

    def test_non_broken_paths_never_add_triage_terms(self) -> None:
        for name, query in TOPIC_QUERIES.items():
            for path in ("GENERAL", "BUILD", "OPERATE"):
                with self.subTest(case=name, path=path):
                    terms = set(self.router._query_terms(query, path))
                    leaked = terms & TRIAGE_TERMS
                    self.assertFalse(leaked, f"{name} leaked {sorted(leaked)} on {path}")

    def test_default_path_argument_is_generic(self) -> None:
        # A caller that forgets the path must get the unexpanded behaviour, never the BROKEN one.
        for query in SYMPTOM_QUERIES.values():
            self.assertFalse(set(self.router._query_terms(query)) & SCOPED_ONLY)

    def test_expansion_is_additive(self) -> None:
        # The original terms must survive: expansion may add, never substitute.
        query = SYMPTOM_QUERIES["memory-leak"]
        generic = self.router._query_terms(query, "GENERAL")
        broken = self.router._query_terms(query, "BROKEN")
        self.assertTrue(set(generic) <= set(broken))

    def test_triage_table_is_separate_from_the_global_synonyms(self) -> None:
        # The topic-dominating bug in reverse: these must never become global synonyms.
        for topic in ("memory", "pool", "queue", "app", "worker", "message", "messages"):
            self.assertNotIn(topic, SYMPTOM_TRIAGE, f"{topic} must not map to triage vocabulary")
            self.assertNotIn(topic, SYNONYMS, f"{topic} must not be a global synonym")

    def test_global_synonyms_are_untouched_by_this_feature(self) -> None:
        # Guard the earlier gains: "slow" belongs to performance, not to failure.
        self.assertEqual(SYNONYMS.get("slow"), ("performance", "latency"))

    def test_the_scoped_contribution_is_non_empty(self) -> None:
        # If every scoped target were already global, the gate tests above would pass vacuously.
        self.assertTrue(SCOPED_ONLY, "the scoped table contributes nothing beyond the global synonyms")

    def test_triage_targets_are_single_words(self) -> None:
        # A multi-word target could never match a document field, so it would be dead weight.
        for target in TRIAGE_TERMS:
            self.assertNotIn(" ", target)


class SymptomRoutingTests(unittest.TestCase):
    """Behaviour: a symptom reaches the resolver, a topic keeps its own domain."""

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="skillrouter-symptom-"))
        self.root = self.temp / "skills"
        for name, description in FIXTURES.items():
            folder = self.root / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {description}\ntags:\n  - engineering\n---\n# {name}\n",
                encoding="utf-8")
        self.config = json.loads(json.dumps(DEFAULT_CONFIG))
        self.config["roots"] = [str(self.root)]
        self.index = self.temp / "index.json"
        Indexer([self.root], self.config, self.index).build()
        self.router = Router(self.config, load_records(self.index))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def _top(self, query: str, limit: int = 5) -> list[str]:
        return [c.name for c in self.router.route(query).candidates[:limit]]

    def test_memory_leak_reaches_a_debugging_skill(self) -> None:
        names = self._top(SYMPTOM_QUERIES["memory-leak"])
        self.assertTrue({"systematic-debugging", "debugging-and-error-recovery"} & set(names),
                        f"no debugging skill retrieved for a memory leak: {names}")

    def test_pool_exhausted_reaches_a_debugging_or_incident_skill(self) -> None:
        names = self._top(SYMPTOM_QUERIES["pool-exhausted"])
        self.assertTrue({"debugging-and-error-recovery", "systematic-debugging", "incident-commander"} & set(names),
                        f"no resolver retrieved for an exhausted pool: {names}")

    def test_app_crashes_reaches_a_debugging_skill(self) -> None:
        names = self._top(SYMPTOM_QUERIES["app-crashes"])
        self.assertTrue({"debugging-and-error-recovery", "systematic-debugging"} & set(names),
                        f"no debugging skill retrieved for a crash: {names}")

    def test_stuck_queue_reaches_a_debugging_skill(self) -> None:
        names = self._top(SYMPTOM_QUERIES["stuck-queue"])
        self.assertTrue({"debugging-and-error-recovery", "systematic-debugging"} & set(names),
                        f"no debugging skill retrieved for a stuck queue: {names}")

    def test_topic_query_keeps_its_own_domain(self) -> None:
        # The important half: designing a memory pool is not a failure, so the debugging skills must not
        # be dragged in ahead of the skills that are actually about memory and pools.
        names = self._top("Design a memory pool for the queue.")
        self.assertTrue(any(name.startswith("memory-") for name in names),
                        f"the memory topic lost its own domain: {names}")
        self.assertNotIn("debugging-and-error-recovery", names,
                         f"a build query pulled in a failure resolver: {names}")

    def test_topic_query_about_databases_keeps_database_skills(self) -> None:
        names = self._top("Design a multi-tenant connection pool for the database.")
        self.assertTrue(any("database" in name for name in names), f"database topic lost: {names}")

    def test_operate_query_on_a_pool_does_not_expand(self) -> None:
        decision = self.router.route("Review our connection pool configuration.")
        self.assertEqual(decision.path, "OPERATE")
        names = [c.name for c in decision.candidates]
        self.assertNotIn("debugging-and-error-recovery", names[:2],
                         f"an OPERATE query pulled in a failure resolver: {names}")

    def test_disabling_triage_disables_the_expansion(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["routing"]["triage"] = False
        router = Router(config, load_records(self.index))
        decision = router.route(SYMPTOM_QUERIES["app-crashes"])
        self.assertEqual(decision.path, "GENERAL")
        self.assertFalse(set(router._query_terms(SYMPTOM_QUERIES["app-crashes"], decision.path)) & SCOPED_ONLY)


class PreviousGainsPreservedTests(unittest.TestCase):
    """The earlier precision work must not be undone by a broader expansion table."""

    def setUp(self) -> None:
        from skillrouter.config import load_config
        if not (Path(__file__).resolve().parents[1] / "work" / "eval-home").exists():
            self.skipTest("no local index configured")
        self.router = Router(load_config())
        if not self.router.records:
            self.skipTest("no local index; run `skillrouter index` to enable suite checks")

    def test_prose_words_alone_still_do_not_select(self) -> None:
        # The 85f12d0 fix: an ordinary prose word shared with a skill must not select it.
        decision = self.router.route("Refactor this module to remove the duplication.")
        if decision.state.value == "USE_SKILL" and decision.primary:
            self.assertIn(decision.primary.name, {"code-simplification", "tech-debt-tracker"},
                          f"prose alone selected {decision.primary.name}")

    def test_no_match_guarantee_holds(self) -> None:
        for query in ("What should I cook for dinner tonight?", "How many moons does Jupiter have?"):
            with self.subTest(query=query):
                self.assertEqual(self.router.route(query).state.value, "NO_MATCH")

    def test_previously_unsafe_pool_case_is_now_safe(self) -> None:
        decision = self.router.route("The database connection pool is exhausted and customers are down.")
        expected = {"incident-commander", "performance-profiler", "incident-response"}
        names = [c.name for c in decision.candidates[:5]]
        self.assertTrue(set(names) & expected, f"expected a resolver in {names}")
        self.assertIn(decision.state.value, ("USE_SKILL", "MULTI_SKILL", "AMBIGUOUS"))

    def test_metrics_do_not_regress(self) -> None:
        cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        refusal = ("NO_MATCH", "AMBIGUOUS")
        rows = []
        for case in cases:
            decision = self.router.route(case["query"])
            names = [c.name for c in decision.candidates]
            rank = next((i + 1 for i, n in enumerate(names) if n in set(case["expected_any"])), None)
            state, kind = decision.state.value, case["kind"]
            if kind == "no-match":
                correct, safe = state == "NO_MATCH", state in refusal
            elif kind == "ambiguous":
                correct = safe = state in refusal
            else:
                correct = rank is not None
                safe = correct or state in refusal
            rows.append({"kind": kind, "state": state, "correct": correct, "safe": safe, "rank": rank,
                         "path": decision.path, "expected_path": case.get("path")})
        pos = [r for r in rows if r["kind"] in ("single", "multi")]
        nm = [r for r in rows if r["kind"] == "no-match"]
        broken = [r for r in rows if r["expected_path"] == "BROKEN"]
        rate = lambda sub, key: sum(r[key] for r in sub) / max(1, len(sub))  # noqa: E731
        self.assertGreaterEqual(rate(nm, "correct"), 0.9, "no-match accuracy regressed")
        self.assertLessEqual(sum(not r["safe"] for r in rows) / len(rows), 0.034, "wrong-skill rate regressed")
        self.assertGreaterEqual(rate(pos, "correct"), 0.919, "Hit@5 regressed")
        self.assertGreaterEqual(sum(r["rank"] == 1 for r in pos) / len(pos), 0.75, "Hit@1 regressed")
        self.assertGreaterEqual(rate(broken, "correct"), 0.9, "BROKEN retrieval regressed")
        self.assertGreaterEqual(
            sum(r["path"] == r["expected_path"] for r in rows if r["expected_path"]) /
            len([r for r in rows if r["expected_path"]]), 0.9, "path accuracy regressed")


if __name__ == "__main__":
    unittest.main()
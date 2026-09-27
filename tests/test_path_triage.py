"""Path triage tests: BROKEN / BUILD / OPERATE / GENERAL.

Triage is lexical and decides threshold selection plus the reason text, so the rules that matter are
whole-word matching, problem-signal precedence, and leading-verb precedence. All offline.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from skillrouter.config import DEFAULT_CONFIG
from skillrouter.engine import PATH_VOCABULARY, Router
from skillrouter.indexer import Indexer, load_records

CASES_PATH = Path(__file__).resolve().parents[1] / "eval" / "claude_skills_cases.json"


class ClassificationTests(unittest.TestCase):
    def classify(self, query: str) -> str:
        return Router._classify_path(query)

    # --- BROKEN ---------------------------------------------------------------

    def test_problem_statements_are_broken(self) -> None:
        for query in ("The checkout API is returning 500 errors in production.",
                      "Our tests are failing on CI.",
                      "The app crashes whenever I open settings.",
                      "We have a regression in login after the last release.",
                      "Users cannot log in and the service is down.",
                      "Investigate a production API performance problem."):
            with self.subTest(query=query):
                self.assertEqual(self.classify(query), "BROKEN")

    def test_a_problem_signal_beats_a_build_verb(self) -> None:
        # An error is decisive even when the sentence also asks for something to be built.
        self.assertEqual(self.classify("Fix the bug and add a regression test."), "BROKEN")

    def test_substring_is_not_a_signal(self) -> None:
        # "zero-downtime" must not read as the BROKEN word "down".
        self.assertEqual(self.classify("Implement a zero-downtime database migration."), "BUILD")
        # "downtime" contains "down" but is not the BROKEN signal; the leading verb decides.
        self.assertNotEqual(self.classify("Plan the downtime window for the upgrade."), "BROKEN")

    # --- BUILD ----------------------------------------------------------------

    def test_construction_tasks_are_build(self) -> None:
        for query in ("Design a RAG system for a large knowledge base.",
                      "Write a Terraform module for an AWS VPC.",
                      "Create a Kubernetes operator for our custom resource.",
                      "Set up Stripe subscription billing for our product.",
                      "Implement a zero-downtime database migration.",
                      "Plan a feature flag rollout for a risky change."):
            with self.subTest(query=query):
                self.assertEqual(self.classify(query), "BUILD")

    # --- OPERATE --------------------------------------------------------------

    def test_review_and_improvement_tasks_are_operate(self) -> None:
        for query in ("Audit the codebase for security vulnerabilities.",
                      "Refactor this module to remove the duplication.",
                      "Optimize the JavaScript bundle size of our frontend.",
                      "Improve our SEO for the marketing site.",
                      "Edit this blog post so it reads better."):
            with self.subTest(query=query):
                self.assertEqual(self.classify(query), "OPERATE")

    def test_leading_verb_wins_over_a_build_noun(self) -> None:
        # "review the design" is OPERATE: the operation verb is the intent, the noun is its object.
        self.assertEqual(self.classify("Review our API design before we ship version two."), "OPERATE")
        self.assertEqual(self.classify("Check our implementation against the OWASP Top 10."), "OPERATE")

    # --- GENERAL --------------------------------------------------------------

    def test_no_operation_verb_is_general(self) -> None:
        self.assertEqual(self.classify("RAG"), "GENERAL")
        self.assertEqual(self.classify(""), "GENERAL")
        self.assertEqual(self.classify("the thing we discussed earlier"), "GENERAL")

    # --- vocabulary integrity -------------------------------------------------

    def test_triage_vocabulary_is_whole_word_usable(self) -> None:
        self.assertEqual(set(PATH_VOCABULARY), {"BROKEN", "BUILD", "OPERATE"})
        # Both lexicons must be present: PATH_LEXICON terms and the WORK_INTENT_EXTRA additions.
        self.assertIn("error", PATH_VOCABULARY["BROKEN"])
        self.assertIn("slow", PATH_VOCABULARY["BROKEN"])
        self.assertIn("design", PATH_VOCABULARY["BUILD"])
        self.assertIn("plan", PATH_VOCABULARY["BUILD"])
        self.assertIn("review", PATH_VOCABULARY["OPERATE"])
        self.assertIn("research", PATH_VOCABULARY["OPERATE"])
        for terms in PATH_VOCABULARY.values():
            self.assertEqual(len(terms), len(set(terms)), "duplicate triage term")


class PathAwareDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="skillrouter-path-"))
        self.root = self.temp / "skills"
        self.index = self.temp / "index.json"
        fixtures = {
            "rag-architect": "Use when designing retrieval augmented generation pipelines.",
            "systematic-debugging": "Use when tests fail, builds break, or behaviour does not match expectations.",
            "pr-review-expert": "Use when reviewing a pull request for security problems.",
            "database-designer": "Use when designing database schemas, indexes and migrations.",
            "performance-profiler": "Use when profiling latency and slow services.",
            "code-simplification": "Use when refactoring code for clarity without changing behaviour.",
            "observability-designer": "Use when designing observability and alerting for a service.",
            "security-auditor": "Use when auditing a codebase for vulnerabilities.",
            # Vocabulary floor: without breadth, ordinary words read as out-of-vocabulary and the
            # corpus-context guard fires for reasons unrelated to path triage.
            "code-reviewer": "Use when reviewing code and modules before a merge.",
            "changelog-generator": "Use when writing a changelog for a release.",
            "test-planner": "Use when planning coverage for a module.",
            "release-planner": "Use when preparing a release document.",
            "incident-reviewer": "Use when reviewing an incident timeline.",
            "backend-reviewer": "Use when reviewing an endpoint in a service.",
            "doc-writer": "Use when documenting a service or module.",
            "refactor-planner": "Use when planning a restructure of a module.",
        }
        for name, description in fixtures.items():
            folder = self.root / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {description}\ntags:\n  - engineering\n---\n# {name}\n",
                encoding="utf-8")
        self.config = json.loads(json.dumps(DEFAULT_CONFIG))
        self.config["roots"] = [str(self.root)]
        Indexer([self.root], self.config, self.index).build()
        self.router = Router(self.config, load_records(self.index))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_decision_exposes_the_path(self) -> None:
        decision = self.router.route("Design a retrieval augmented generation pipeline.")
        self.assertEqual(decision.path, "BUILD")
        self.assertEqual(decision.to_dict()["path"], "BUILD")

    def test_broken_path_is_reported_on_a_problem_statement(self) -> None:
        decision = self.router.route("Our tests fail and the build breaks on CI.")
        self.assertEqual(decision.path, "BROKEN")
        self.assertIsNotNone(decision.primary)
        self.assertIn("problem statement", decision.reason.lower())

    def test_reason_text_names_the_path(self) -> None:
        decision = self.router.route("Audit a codebase for vulnerabilities.")
        self.assertEqual(decision.path, "OPERATE")
        if decision.primary:
            self.assertIn("OPERATE", decision.reason)

    def test_path_threshold_cannot_loosen_the_configured_use_threshold(self) -> None:
        # A path may only raise the bar; a lower configured value must be ignored.
        config = json.loads(json.dumps(self.config))
        config["confidence"] = dict(config["confidence"], use=0.62, paths={"BUILD": {"use": 0.1}})
        router = Router(config, load_records(self.index))
        decision = router.route("Design a retrieval augmented generation pipeline.")
        # With the path threshold ignored, the normal use threshold applies and the skill still wins.
        self.assertEqual(decision.primary.name if decision.primary else None, "rag-architect")

    def test_name_anchored_match_is_not_demoted_by_path_threshold(self) -> None:
        # A query that names the skill is trustworthy regardless of how the task is phrased, so the
        # BROKEN path must not raise the bar against it, and the reason must not claim that it did.
        decision = self.router.route("Why does my performance-profiler report wrong numbers?")
        self.assertEqual(decision.path, "BROKEN")
        self.assertEqual(decision.primary.name if decision.primary else None, "performance-profiler")
        self.assertNotIn("raised threshold", decision.reason)

    def test_triage_can_be_disabled_by_config(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["routing"]["triage"] = False
        router = Router(config, load_records(self.index))
        self.assertEqual(router.route("Design a retrieval augmented generation pipeline.").path, "GENERAL")


class CuratedPathSuiteTests(unittest.TestCase):
    def setUp(self) -> None:
        from skillrouter.config import load_config
        if not load_config().get("roots") or not (Path(__file__).resolve().parents[1] / "work" / "eval-home").exists():
            self.skipTest("no local index configured")
        self.router = Router(load_config())
        if not self.router.records:
            self.skipTest("no local index; run `skillrouter index` to enable suite checks")
        self.cases = [c for c in json.loads(CASES_PATH.read_text(encoding="utf-8")) if c.get("path")]

    def test_path_accuracy_holds_on_the_curated_suite(self) -> None:
        correct = sum(self.router._classify_path(c["query"]) == c["path"] for c in self.cases)
        rate = correct / len(self.cases)
        self.assertGreaterEqual(rate, 0.9, f"path accuracy regressed to {rate:.3f} ({correct}/{len(self.cases)})")

    def test_every_path_label_is_exercised(self) -> None:
        labels = {c["path"] for c in self.cases}
        self.assertEqual(labels, {"BROKEN", "BUILD", "OPERATE"})


if __name__ == "__main__":
    unittest.main()
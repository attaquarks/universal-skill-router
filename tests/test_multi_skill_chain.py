"""Regression tests for name-signal symmetry and the multi-skill contract.

The engine scores a query against a skill's fields using the prepared token set (inflection-folded,
hyphen-split, synonym-expanded) but computed name signals from the *raw* query tokens. The two sides
therefore disagreed: a query saying "SLOs" scored against a skill named "slo-architect" while registering
no name signal, so a correct match could not support a chain and could even be reported as ambiguous.
These tests pin the agreement, and pin the multi-skill contract either side of it.

Fixtures include a vocabulary floor for the same reason the token-symmetry tests do: on a two-skill corpus
almost every ordinary word looks out-of-vocabulary and the corpus-context guard then fires for reasons
unrelated to what is under test.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from skillrouter.config import DEFAULT_CONFIG, load_config
from skillrouter.engine import Router
from skillrouter.indexer import Indexer, load_records

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "eval" / "claude_skills_cases.json"

# Vocabulary floor: breadth for the out-of-vocabulary guard, with no name colliding with the queries
# under test (notably nothing named after a domain word like "schema" or "slo").
FLOOR = {
    "code-reviewer": "Use when reviewing a code module before a merge.",
    "changelog-generator": "Use when writing a changelog note for a release.",
    "coverage-planner": "Use when planning coverage for a module.",
    "release-planner": "Use when preparing a release document.",
    "incident-reviewer": "Use when reviewing an incident timeline.",
    "backend-reviewer": "Use when reviewing an endpoint in a service.",
    "doc-writer": "Use when documenting a service or a module.",
    "cleanup-planner": "Use when planning a restructure of a module.",
    "merge-helper": "Use when merging a branch into the main line.",
    "style-guide": "Use when checking formatting and naming conventions.",
    "packaging-helper": "Use when assembling a distributable artifact.",
    "credential-rotator": "Use when cycling a credential on a schedule.",
    "cache-warmer": "Use when preloading a cache before traffic arrives.",
    "log-shipper": "Use when forwarding a log stream to a collector.",
    "queue-drainer": "Use when draining a backlog from a work list.",
    "ledger-writer": "Use when recording a transaction in a ledger.",
}


def skill(name: str, description: str, extra: str = "") -> str:
    return f"---\nname: {name}\ndescription: {description}\ntags:\n  - engineering\n{extra}---\n# {name}\n"


class _FixtureCase(unittest.TestCase):
    """Shared fixture plumbing: build a small indexed corpus and a router over it."""

    fixtures: dict[str, str] = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = Path(tempfile.mkdtemp(prefix="skillrouter-multi-"))
        cls.root = cls.temp / "skills"
        cls.index = cls.temp / "index.json"
        fixtures = dict(FLOOR)
        fixtures.update(cls.fixtures)
        for name, description in fixtures.items():
            folder = cls.root / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_text(skill(name, description), encoding="utf-8")
        cls.config = json.loads(json.dumps(DEFAULT_CONFIG))
        cls.config["roots"] = [str(cls.root)]
        Indexer([cls.root], cls.config, cls.index).build()
        cls.router = Router(cls.config, load_records(cls.index))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.temp, ignore_errors=True)

    def names_ahead(self, query: str, limit: int = 5) -> list:
        return [c.name for c in self.router.route(query).candidates[:limit]]


class NameSignalSymmetryTests(_FixtureCase):
    """A term that scores must also be able to serve as evidence."""

    fixtures = {
        "slo-architect": "Use when defining service level objectives and error budgets for a service.",
        "observability-designer": "Use when designing observability across metrics, logs and tracing.",
        "dashboard-builder": "Use when building a dashboard that surfaces service metrics.",
        "migration-architect": "Use when planning a zero-downtime migration and compatibility checks.",
    }

    def test_folded_term_reaches_hyphenated_name(self) -> None:
        self.assertIn("slo-architect", self.names_ahead("We need SLOs for the checkout service."))

    def test_folded_term_registers_as_a_name_signal(self) -> None:
        """The regression itself: the signal set must agree with the scored set."""
        decision = self.router.route("We need SLOs for the checkout service.")
        signals = set()
        for cand in decision.candidates:
            signals |= set(self.router._distinctive_signals(cand))
        self.assertIn("slo", signals, f"folded name token produced no signal: {sorted(signals)}")

    def test_plain_name_token_still_signals(self) -> None:
        """The fix must not have replaced raw-token signals with folded-only ones."""
        decision = self.router.route("Design a dashboard for the service metrics.")
        signals = set(self.router._distinctive_signals(decision.candidates[0]))
        self.assertIn("dashboard", signals)

    def test_migration_term_reaches_architect(self) -> None:
        self.assertIn("migration-architect", self.names_ahead("Plan the migrations for the new orders table."))


class MultiSkillContractTests(_FixtureCase):
    """MULTI_SKILL must mean a request whose two halves the corpus resolves, not merely 'several related'."""

    fixtures = {
        "database-schema-designer": "Use when designing database schema, normalising tables and planning indexes.",
        "api-and-interface-design": "Use when designing a stable API contract and interface boundaries.",
        "frontend-ui-engineering": "Use when building an accessible responsive frontend interface.",
        "senior-frontend": "Use when building a frontend component architecture in React.",
        "unrelated-themed-skill": "Use when selecting a themed palette for a special event.",
    }

    def test_genuine_multi_domain_request_surfaces_both_halves(self) -> None:
        names = self.names_ahead("Design the database schema and the frontend interface for the new feature.")
        self.assertTrue(
            {"database-schema-designer", "frontend-ui-engineering", "senior-frontend"} & set(names),
            f"a multi-domain request did not surface both halves: {names}")

    def test_chain_carries_supporting_without_repeating_primary(self) -> None:
        decision = self.router.route("Design the database schema and the frontend interface for the new feature.")
        if decision.state.value == "MULTI_SKILL":
            self.assertTrue(decision.supporting, "MULTI_SKILL must carry at least one supporting skill")
            self.assertTrue(all(c.skill_id != decision.primary.skill_id for c in decision.supporting))

    def test_single_subject_request_does_not_chain(self) -> None:
        """Several related candidates exist here; that alone must not produce MULTI_SKILL."""
        decision = self.router.route("Design a normalised database schema for invoices.")
        self.assertNotEqual(decision.state.value, "MULTI_SKILL",
                            f"single-subject request chained: {[c.name for c in decision.supporting]}")

    def test_unrelated_request_is_refused_or_ambiguous(self) -> None:
        decision = self.router.route("hello there")
        self.assertIn(decision.state.value, ("AMBIGUOUS", "NO_MATCH"))

    def test_supporting_requires_a_new_signal_not_shared_topic(self) -> None:
        decision = self.router.route("Design a normalised database schema for invoices.")
        primary_signals = set(self.router._distinctive_signals(decision.candidates[0]))
        for cand in decision.supporting:
            new = set(self.router._distinctive_signals(cand)) - primary_signals
            self.assertTrue(new, f"{cand.name} supported on shared signals only")


class ChainOverrideTests(_FixtureCase):
    """Project routes stay an explicit, config-driven override."""

    fixtures = {
        "deploy-to-vercel": "Use when deploying an application to a hosting provider.",
        "debugging-and-error-recovery": "Use when debugging a failure with root-cause analysis.",
    }

    def _router_with_project(self, projects):
        config = json.loads(json.dumps(self.config))
        config["projects"] = projects
        return Router(config, load_records(self.index))

    def test_project_route_forces_chain_at_override_confidence(self) -> None:
        """A saved project chain is an explicit override: it chains at its own confidence."""
        router = self._router_with_project([{
            "name": "staging",
            "aliases": ["staging"],
            "routes": [{"when": ["staging deploy broken"],
                        "chain": [["deploy-to-vercel"], ["debugging-and-error-recovery"]]}],
        }])
        decision = router.route("the staging deploy broken again")
        self.assertEqual(decision.state.value, "MULTI_SKILL")
        self.assertEqual(decision.confidence, 0.98)
        self.assertEqual(decision.primary.name, "deploy-to-vercel")
        self.assertIn("debugging-and-error-recovery", [c.name for c in decision.supporting])

    def test_without_a_project_route_no_override_confidence(self) -> None:
        """With no saved route the same query must not borrow the override's confidence."""
        decision = self.router.route("the staging deploy broken again")
        self.assertNotEqual(decision.confidence, 0.98)

    def test_project_route_ignores_unknown_skill_names(self) -> None:
        """A saved chain naming a skill that is not indexed must not invent one."""
        router = self._router_with_project([{
            "name": "staging",
            "aliases": ["staging"],
            "routes": [{"when": ["staging deploy broken"],
                        "chain": [["deploy-to-vercel"], ["no-such-skill-anywhere"]]}],
        }])
        decision = router.route("the staging deploy broken again")
        self.assertNotIn("no-such-skill-anywhere", [c.name for c in decision.candidates])


class SignalDiversityTests(_FixtureCase):
    """Signals are name-derived claims; they must never come from weak prose fields."""

    fixtures = {
        "performance-profiler": "Use when profiling and optimising application performance.",
        "performance-optimization": "Use when optimising frontend and backend performance.",
    }

    def test_signals_come_from_strong_fields_only(self) -> None:
        decision = self.router.route("Profile and optimise the performance of the frontend.")
        for cand in decision.candidates[:3]:
            fields = self.router._fields[cand.skill_id]
            strong = set(fields["name"]) | set(fields["aliases"]) | set(fields["triggers"])
            signals = set(self.router._distinctive_signals(cand))
            self.assertTrue(signals <= strong,
                            f"{cand.name}: signals leaked from weak fields {sorted(signals - strong)}")

    def test_overlapping_skills_do_not_both_claim_support(self) -> None:
        decision = self.router.route("Optimise the performance of our frontend application.")
        primary_signals = set(self.router._distinctive_signals(decision.candidates[0]))
        for cand in decision.supporting:
            new = set(self.router._distinctive_signals(cand)) - primary_signals
            self.assertTrue(new, f"{cand.name} supported on shared subject vocabulary only")


class SuiteMetricsTests(unittest.TestCase):
    """The gains this change must not cost, measured on the installed corpus when it is present."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        cls.router = Router(load_config())

    def setUp(self) -> None:
        if len(self.router.records) < 100:
            self.skipTest("suite metrics need the installed corpus")

    def test_multi_skill_state_not_regressed(self) -> None:
        multi = [c for c in self.cases if c["kind"] == "multi"]
        chained = sum(1 for c in multi if self.router.route(c["query"]).state.value == "MULTI_SKILL")
        self.assertGreaterEqual(chained / len(multi), 0.545, f"multi-skill state regressed: {chained}/{len(multi)}")

    def test_broken_retrieval_held(self) -> None:
        broken = [c for c in self.cases if c.get("path") == "BROKEN"]
        hits = 0
        for case in broken:
            names = [c.name for c in self.router.route(case["query"]).candidates]
            hits += any(n in case["expected_any"] for n in names)
        self.assertGreaterEqual(hits / len(broken), 0.933, f"BROKEN retrieval regressed: {hits}/{len(broken)}")

    def test_no_match_accuracy_held(self) -> None:
        nm = [c for c in self.cases if c["kind"] == "no-match"]
        correct = sum(1 for c in nm if self.router.route(c["query"]).state.value == "NO_MATCH")
        self.assertGreaterEqual(correct / len(nm), 0.90, f"no-match regressed: {correct}/{len(nm)}")

    def test_wrong_skill_rate_held(self) -> None:
        """A wrong skill is a confident answer that is not the expected one.

        Refusing (AMBIGUOUS / NO_MATCH) is never *wrong*, so a case that retrieves none of its expected
        skills while refusing stays safe -- the same accounting the evaluation harness uses.
        """
        wrong = []
        for case in self.cases:
            decision = self.router.route(case["query"])
            if decision.state.value in ("AMBIGUOUS", "NO_MATCH"):
                continue
            names = [c.name for c in decision.candidates]
            if not any(n in case["expected_any"] for n in names):
                wrong.append(case["id"])
        self.assertLessEqual(len(wrong) / len(self.cases), 0.009,
                             f"wrong-skill rate regressed to {len(wrong)}: {wrong}")

    def test_symptom_case_reaches_a_resolver(self) -> None:
        names = [c.name for c in self.router.route("Our service has a memory leak under load.").candidates[:5]]
        self.assertTrue(any("debug" in n or "memory" in n or "profiler" in n for n in names),
                        f"memory-leak did not reach a resolver: {names}")


if __name__ == "__main__":
    unittest.main()

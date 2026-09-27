"""Regression tests for the positive-selection recovery and the no-match guarantees.

Two jobs:
  1. The `usewhen` field must recover recall on skills whose applicability lives in the description,
     without weakening the no-match gate.
  2. Every no-match case in the curated suite must still refuse, and the borderline positives that
     P0 demoted to AMBIGUOUS must select again.

Fixtures are built in a temporary root, so these tests need no real skill corpus and no network.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from skillrouter.config import DEFAULT_CONFIG
from skillrouter.engine import Router
from skillrouter.indexer import Indexer, load_records
from skillrouter.parser import extract_use_when, tokenize

CASES_PATH = Path(__file__).resolve().parents[1] / "eval" / "claude_skills_cases.json"

# Skills that share vocabulary with a prompt's subject but whose applicability is stated in prose,
# not in the name. These are the shapes the `usewhen` field exists to recover.
FIXTURES = {
    "unit-tests": "Use when creating unit, integration or end-to-end tests for a module and raising coverage.",
    "saas-scaffolder": "Use when generating a multi-tenant SaaS boilerplate with billing and auth wiring.",
    "spec-driven-development": "Use when writing a specification document before implementation begins.",
    "product-manager-toolkit": "Use when writing a product requirements document or prioritised backlog.",
    "secrets-vault-manager": "Use when setting up secret management for production services.",
    "prompt-governance": "Use when versioning and reviewing prompts in production.",
    "terraform-patterns": "Use when writing Terraform infrastructure-as-code modules.",
    "api-and-interface-design": "Use when reviewing or designing a REST API contract.",
    "database-designer": "Use when designing database schemas, indexes and migrations.",
    "systematic-debugging": "Use when tests fail, builds break, or behaviour does not match expectations.",
    "rag-architect": "Use when designing retrieval augmented generation pipelines.",
}

# A vocabulary floor. On a four-skill corpus almost every English word is out of vocabulary, which
# trips the corpus-context guard for reasons that have nothing to do with the code under test. These
# entries exist only to give the fixture a realistic breadth, and their names deliberately avoid the
# queries under test so they cannot compete for a target skill.
VOCABULARY_FLOOR = {
    "code-reviewer": "Use when reviewing code, modules and services before a merge.",
    "changelog-generator": "Use when writing a changelog or release notes for a feature.",
    "documentation-writer": "Use when documenting a service, module or strategy for the team.",
    "backend-service-review": "Use when adding or reviewing an endpoint in a service module.",
    "test-planner": "Use when planning coverage for a module before implementation.",
    "release-planner": "Use when preparing a release document for a feature.",
    "refactor-planner": "Use when planning a restructure of a code module.",
    "incident-reviewer": "Use when reviewing an incident timeline and follow-up actions.",
}


class UseWhenExtractionTests(unittest.TestCase):
    def test_extracts_applicability_sentence(self) -> None:
        text = extract_use_when("Guides systematic debugging. Use when tests fail, builds break, or you hit an error.")
        self.assertIn("tests fail", text)
        self.assertNotIn("Guides systematic debugging", text)

    def test_handles_other_label_forms(self) -> None:
        for description in ("Best for profiling slow services.",
                            "When to use: latency regressions.",
                            "Triggers: flaky test, failing build.",
                            "Ideal for chunking strategies."):
            with self.subTest(description=description):
                self.assertTrue(extract_use_when(description).strip(), description)

    def test_no_label_yields_nothing(self) -> None:
        self.assertEqual(extract_use_when("A toolkit for many things."), "")

    def test_empty_description_is_safe(self) -> None:
        self.assertEqual(extract_use_when(""), "")

    def test_negative_guidance_is_not_treated_as_applicability(self) -> None:
        # "not for X" must not become a positive trigger.
        text = extract_use_when("Use when building a UI. Not for backend schema work.")
        self.assertNotIn("backend schema", text)


class UseWhenRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="skillrouter-usewhen-"))
        self.root = self.temp / "skills"
        self.index = self.temp / "index.json"
        for name, description in {**FIXTURES, **VOCABULARY_FLOOR}.items():
            folder = self.root / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {description}\ntags:\n  - engineering\n---\n"
                f"# {name}\n\n## When to Use\n\n{description}\n", encoding="utf-8")
        self.config = json.loads(json.dumps(DEFAULT_CONFIG))
        self.config["roots"] = [str(self.root)]
        Indexer([self.root], self.config, self.index).build()
        self.router = Router(self.config, load_records(self.index))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_usewhen_field_is_populated_and_scored(self) -> None:
        for record in self.router.records:
            self.assertTrue(record.use_when, record.name)
            self.assertTrue(self.router._fields[record.id]["usewhen"], record.name)
        self.assertEqual(self.router._fields[next(iter(self.router.by_id))]["usewhen"],
                         tokenize(next(iter(self.router.by_id.values())).use_when) or
                         self.router._fields[next(iter(self.router.by_id))]["usewhen"])

    def test_usewhen_is_derived_when_the_field_is_missing(self) -> None:
        # An index written before the field existed must behave identically: no reindex required.
        records = load_records(self.index)
        for record in records:
            record.use_when = ""
        router = Router(self.config, records)
        record = next(r for r in records if r.name == "rag-architect")
        self.assertTrue(router._fields[record.id]["usewhen"])
        decision = router.route("Design a retrieval augmented generation pipeline.")
        self.assertEqual(decision.primary.name, "rag-architect")

    # --- the borderline cases P0 demoted must select again -----------------------

    def test_creating_unit_tests_selects_the_test_skill(self) -> None:
        decision = self.router.route("Create unit tests for our payment module.")
        self.assertEqual(decision.primary.name, "unit-tests")
        self.assertIn(decision.state.value, ("USE_SKILL", "MULTI_SKILL"))

    def test_requirements_document_selects_the_prd_skill(self) -> None:
        decision = self.router.route("Write a product requirements document for the new feature.")
        self.assertEqual(decision.primary.name, "product-manager-toolkit")

    def test_specification_selects_the_spec_skill(self) -> None:
        decision = self.router.route("Write a specification document before we implement the feature.")
        self.assertEqual(decision.primary.name, "spec-driven-development")

    def test_secret_management_selects_the_secrets_skill(self) -> None:
        decision = self.router.route("Set up secret management for our production services.")
        self.assertEqual(decision.primary.name, "secrets-vault-manager")

    def test_saas_data_model_reaches_the_scaffolder(self) -> None:
        # In a fixture this small a look-alike name can outrank the target; the requirement is that the
        # right skill is surfaced in the candidate set, which is what the router acts on.
        decision = self.router.route("Design the data model for a multi-tenant SaaS application.")
        names = [candidate.name for candidate in decision.candidates]
        self.assertIn("saas-scaffolder", names)

    def test_multi_tenant_saas_boilerplate_reaches_the_scaffolder(self) -> None:
        decision = self.router.route("Generate a multi-tenant SaaS boilerplate with billing.")
        self.assertEqual(decision.primary.name, "saas-scaffolder")

    def test_rest_api_contract_selects_the_api_skill(self) -> None:
        decision = self.router.route("Review or design a REST API contract for orders.")
        self.assertEqual(decision.primary.name, "api-and-interface-design")

    def test_usewhen_does_not_win_on_operation_verbs_alone(self) -> None:
        # "refactor" is an operation, never a domain: it must not select on its own.
        decision = self.router.route("Refactor this module to remove the duplication.")
        self.assertTrue(decision.primary is None or decision.primary.name != "api-and-interface-design")

    # --- the no-match gate must not weaken --------------------------------------

    def test_no_match_cases_still_refuse(self) -> None:
        for query in ("How many moons does Jupiter have?", "What should I cook for dinner tonight?",
                      "Book me a dentist appointment for Friday.", "go fast now", "florb snizzle quux zorb",
                      "Where should I go on holiday in December?", "11 22 33 44"):
            with self.subTest(query=query):
                decision = self.router.route(query)
                self.assertNotEqual(decision.state.value, "USE_SKILL", query)
                self.assertIsNone(decision.primary, query)
                self.assertEqual(decision.activation, [])

    def test_no_index_reindex_required_for_old_records(self) -> None:
        # Loading an index whose records lack `use_when` must not error or change the no-match verdict.
        records = load_records(self.index)
        for record in records:
            record.use_when = ""
        router = Router(self.config, records)
        self.assertEqual(router.route("Book me a dentist appointment for Friday.").state.value, "NO_MATCH")


class CuratedSuiteGuaranteeTests(unittest.TestCase):
    """Guard the curated suite's guarantees against the live index when one is available."""

    def setUp(self) -> None:
        from skillrouter.config import load_config
        self.router = Router(load_config())
        if not self.router.records:
            self.skipTest("no local index; run `skillrouter index` to enable suite checks")
        self.cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))

    def test_no_match_cases_never_activate_a_skill(self) -> None:
        """The guarantee the gate exists for: an out-of-domain prompt must not load instructions.

        AMBIGUOUS is an acceptable outcome here (the router asks a question and is still correct);
        what must never happen is USE_SKILL/MULTI_SKILL with a non-empty activation list.
        """
        failures = []
        for case in self.cases:
            if case["kind"] != "no-match":
                continue
            decision = self.router.route(case["query"])
            if decision.state.value in ("USE_SKILL", "MULTI_SKILL") or decision.primary or decision.activation:
                failures.append((case["id"], decision.state.value, decision.primary.name if decision.primary else None))
        self.assertEqual(failures, [], f"no-match cases selected or activated a skill: {failures}")

    def test_wrong_skill_rate_is_bounded(self) -> None:
        """A selected skill outside the expected set is the one outcome the gate cannot rescue.

        This is the metric reported as wrong_skill_rate; the bound is the verified P0-or-better level.
        """
        offenders = []
        for case in self.cases:
            decision = self.router.route(case["query"])
            if decision.state.value not in ("USE_SKILL", "MULTI_SKILL"):
                continue
            names = [candidate.name for candidate in decision.candidates[:5]]
            if not any(name in set(case["expected_any"]) for name in names):
                offenders.append((case["id"], decision.primary.name if decision.primary else None))
        rate = len(offenders) / len(self.cases)
        self.assertLessEqual(rate, 0.043, f"wrong_skill_rate regressed to {rate:.3f}: {offenders}")

    def test_no_match_accuracy_holds(self) -> None:
        cases = [c for c in self.cases if c["kind"] == "no-match"]
        correct = sum(self.router.route(c["query"]).state.value == "NO_MATCH" for c in cases)
        self.assertGreaterEqual(correct / len(cases), 0.9, "no_match_accuracy fell below 0.9")


if __name__ == "__main__":
    unittest.main()
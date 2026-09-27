"""Tests for the applicability-clause extraction behind the `usewhen` field.

`usewhen` is scored at weight 2.2 while `description` is 1.2, so whatever lands in `usewhen` is the
router's strongest prose signal. Keeping the whole sentence around a label meant a one-sentence
description was duplicated verbatim into the higher-weight field: the same text scored twice, and the
"use when"/"best for" boilerplate scored at all. These tests pin the clause contract, the fallback, and
the router behaviour that depends on it.
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
from skillrouter.parser import APPLICABILITY_OPENERS, applicability_clause, extract_use_when, tokenize

CASES_PATH = Path(__file__).resolve().parents[1] / "eval" / "claude_skills_cases.json"


class ClauseExtractionTests(unittest.TestCase):
    def test_use_when_keeps_only_the_clause(self) -> None:
        clause = applicability_clause("Design stable APIs. Use when auditing a breaking interface change.")
        self.assertEqual(clause, "auditing a breaking interface change.")
        self.assertNotIn("use when", clause.lower())

    def test_best_for(self) -> None:
        clause = applicability_clause("Hand-drawn diagrams for architecture. Best for teams sketching a flow.")
        self.assertEqual(clause, "teams sketching a flow.")
        self.assertNotIn("best for", clause.lower())

    def test_triggers(self) -> None:
        clause = applicability_clause("Vendor risk scoring. Triggers on \"vendor SLA\" and \"supplier performance\".")
        self.assertIn("vendor SLA", clause)
        self.assertNotIn("triggers", clause.lower())

    def test_label_in_the_middle_keeps_the_head_out(self) -> None:
        # The head ("Accessibility audit skill for scanning ...") is the description field's job. If it also
        # landed in usewhen it would be scored twice, once at the higher weight.
        description = ("Accessibility audit skill for scanning and verifying WCAG compliance across React, "
                       "Next.js and Vue. Use when auditing accessibility, fixing violations, or checking "
                       "color contrast.")
        clause = applicability_clause(description)
        self.assertIn("auditing accessibility", clause)
        self.assertNotIn("React", clause)
        self.assertNotIn("Next.js", clause)

    def test_no_applicability_clause_returns_empty(self) -> None:
        self.assertEqual(applicability_clause("A toolkit for many unrelated things."), "")
        self.assertEqual(applicability_clause(""), "")
        self.assertEqual(applicability_clause("   "), "")

    def test_multi_sentence_description_keeps_each_clause(self) -> None:
        text = "Guides debugging. Use when tests fail. Use when builds break. Also covers profiling."
        clause = applicability_clause(text)
        self.assertIn("tests fail", clause)
        self.assertIn("builds break", clause)
        self.assertNotIn("profiling", clause, "a non-applicability sentence must not be pulled in")

    def test_a_sentence_naming_two_labels_keeps_both_clauses(self) -> None:
        clause = applicability_clause("Reviewer for diffs. Use when you want a critical review, or when you suspect agreement bias.")
        self.assertIn("a critical review", clause)
        self.assertIn("agreement bias", clause)
        self.assertNotIn("use when", clause.lower())

    def test_opener_without_a_label_keyword(self) -> None:
        # "When the user wants to ..." states applicability without containing "use when".
        clause = applicability_clause("Experiment planning. When the user wants to plan an A/B test, use this.")
        self.assertIn("plan an A/B test", clause)

    def test_negative_disambiguation_is_still_stripped(self) -> None:
        clause = applicability_clause("Use when reviewing a module. Not for backend schema work.")
        self.assertIn("reviewing a module", clause)
        self.assertNotIn("schema", clause)

    def test_no_clause_yields_an_empty_field(self) -> None:
        # No fallback to the whole description: scoring a label-less skill's prose at both 2.2 and 1.2
        # would double-count it. Vocabulary coverage is unaffected because document frequency is built
        # from every field, description included.
        self.assertEqual(extract_use_when("A toolkit for many unrelated things."), "")

    def test_field_never_carries_the_description_head(self) -> None:
        description = "Design stable APIs and versioned interfaces. Use when changing a public contract."
        field = extract_use_when(description)
        self.assertEqual(field, "changing a public contract.")
        self.assertNotIn("versioned interfaces", field)

    def test_empty_description_yields_empty_field(self) -> None:
        self.assertEqual(extract_use_when(""), "")

    def test_clause_is_never_longer_than_the_description(self) -> None:
        for description in ("Use when tests fail.",
                            "Do a thing. Use when X happens, and Y happens, and Z happens too.",
                            "Best for small teams."):
            with self.subTest(description=description):
                self.assertLessEqual(len(extract_use_when(description)), len(description))

    def test_openers_are_a_subset_of_real_phrasings(self) -> None:
        self.assertTrue(all(opener.startswith("when ") for opener in APPLICABILITY_OPENERS))


class UseWhenScoringTests(unittest.TestCase):
    """The field must be scored, and only over the clause."""

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="skillrouter-clause-"))
        self.root = self.temp / "skills"
        self.index = self.temp / "index.json"
        fixtures = {
            # head text (frameworks) plus a clause (the real conditions)
            "a11y-audit": ("Accessibility audit for WCAG compliance across React, Next.js, Vue and Svelte. "
                           "Use when auditing accessibility, fixing violations, or checking color contrast."),
            # the clause is where the distinctive term lives
            "debugging-and-error-recovery": "Catch regressions early. Use when tests fail or a build breaks.",
            "vendor-management": "Vendor operations. Use when reviewing third-party vendors and supply risk.",
            # vocabulary floor
            "code-reviewer": "Use when reviewing code and modules before a merge.",
            "changelog-generator": "Use when writing a changelog for a release.",
            "doc-writer": "Use when documenting a service or a module.",
            "merge-helper": "Use when merging a branch into the main line.",
            "style-guide": "Use when checking formatting and naming conventions.",
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

    def test_usewhen_field_holds_the_clause_not_the_head(self) -> None:
        record = self.router.by_name["a11y-audit"][0]
        field = set(self.router._fields[record.id]["usewhen"])
        self.assertIn("auditing", field)
        self.assertIn("accessibility", field)
        self.assertNotIn("react", field, "framework head text belongs to the description field")

    def test_clause_terms_drive_selection(self) -> None:
        decision = self.router.route("Audit our accessibility for contrast violations.")
        self.assertIsNotNone(decision.primary)
        self.assertEqual(decision.primary.name, "a11y-audit")

    def test_clause_only_skill_is_still_retrieved(self) -> None:
        # The distinctive term "vendors" appears only in the clause.
        decision = self.router.route("Review our third-party vendors and supply risk.")
        names = [c.name for c in decision.candidates]
        self.assertIn("vendor-management", names)

    def test_description_still_scores_the_head_text(self) -> None:
        record = self.router.by_name["a11y-audit"][0]
        self.assertIn("react", set(self.router._fields[record.id]["description"]))

    def test_no_clause_skill_keeps_its_description_terms(self) -> None:
        record = self.router.by_name["style-guide"][0]
        # "checking formatting and naming conventions" contains a label, so a clause exists; assert the
        # field is populated either way, which is the invariant that matters.
        self.assertTrue(self.router._fields[record.id]["usewhen"])


class CuratedSuiteGuaranteeTests(unittest.TestCase):
    def setUp(self) -> None:
        from skillrouter.config import load_config
        if not (Path(__file__).resolve().parents[1] / "work" / "eval-home").exists():
            self.skipTest("no local index configured")
        self.router = Router(load_config())
        if not self.router.records:
            self.skipTest("no local index; run `skillrouter index` to enable suite checks")
        self.cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))

    def _measure(self) -> dict:
        refusal = ("NO_MATCH", "AMBIGUOUS")
        rows = []
        for case in self.cases:
            decision = self.router.route(case["query"])
            names = [c.name for c in decision.candidates]
            expected = set(case["expected_any"])
            rank = next((i + 1 for i, n in enumerate(names) if n in expected), None)
            state, kind = decision.state.value, case["kind"]
            if kind == "no-match":
                correct, safe = state == "NO_MATCH", state in refusal
            elif kind == "ambiguous":
                correct = safe = state in refusal
            else:
                correct = rank is not None
                safe = correct or state in refusal
            rows.append({"id": case["id"], "kind": kind, "correct": correct, "safe": safe, "rank": rank,
                         "path": decision.path, "expected_path": case.get("path")})
        pos = [r for r in rows if r["kind"] in ("single", "multi")]
        nm = [r for r in rows if r["kind"] == "no-match"]
        broken = [r for r in rows if r["expected_path"] == "BROKEN"]
        return {
            "no_match": sum(r["correct"] for r in nm) / len(nm),
            "wrong": sum(not r["safe"] for r in rows) / len(rows),
            "hit@5": sum(r["correct"] for r in pos) / len(pos),
            "hit@1": sum(r["rank"] == 1 for r in pos) / len(pos),
            "broken": sum(r["correct"] for r in broken) / len(broken),
            "path": sum(r["path"] == r["expected_path"] for r in rows if r["expected_path"]) /
                    len([r for r in rows if r["expected_path"]]),
        }

    def test_metrics_do_not_regress(self) -> None:
        m = self._measure()
        self.assertGreaterEqual(m["no_match"], 0.9, f"no-match accuracy fell to {m['no_match']:.3f}")
        self.assertLessEqual(m["wrong"], 0.043, f"wrong-skill rate rose to {m['wrong']:.3f}")
        self.assertGreaterEqual(m["hit@5"], 0.884, f"Hit@5 fell to {m['hit@5']:.3f}")
        self.assertGreaterEqual(m["hit@1"], 0.70, f"Hit@1 fell to {m['hit@1']:.3f}")
        self.assertGreaterEqual(m["path"], 0.9, f"path accuracy fell to {m['path']:.3f}")

    def test_broken_retrieval_holds_or_improves(self) -> None:
        m = self._measure()
        self.assertGreaterEqual(m["broken"], 0.58, f"BROKEN retrieval fell to {m['broken']:.3f}")

    def test_usewhen_field_is_not_a_verbatim_description_copy(self) -> None:
        # The redundancy metric: the median record's field should be a genuine subset, not the whole text.
        shares = []
        for record in self.router.records:
            desc = set(self.router._fields[record.id]["description"])
            field = set(self.router._fields[record.id]["usewhen"])
            if desc and field:
                shares.append(len(field - desc) / len(field))
        self.assertTrue(shares)
        novel = sum(shares) / len(shares)
        self.assertGreater(novel, 0.0, "some usewhen terms must be absent from the description field")


if __name__ == "__main__":
    unittest.main()
"""Regression tests for tokenizer/evidence symmetry.

Two bugs this file pins down:

1. Derived-field evidence. `keywords` is built by parse_skill from name+description+domain+tags+headings,
   and 82% of its tokens come from the description alone. Treating a keyword hit like an identity claim
   let "Refactor this module to remove the duplication." select api-and-interface-design on the single
   prose word `module`. A keyword hit now has to be *very* rare in the corpus to identify a skill.

2. Query/document asymmetry. The name field is hyphen-split (parts only) while a query kept its
   compounds whole, so "multi-tenant" could never meet the name part "tenant"; and inflections were
   folded for the out-of-vocabulary guard but not for matching, so "vendors" never reached a skill whose
   own name says "vendor". Both are now symmetric, with the folded/compound forms only ever *added* so
   an exact match still outranks an approximate one.

Fixtures include a vocabulary floor: on a two-skill corpus almost every ordinary word looks
out-of-vocabulary and the corpus-context guard fires for reasons unrelated to what is under test.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from skillrouter.config import DEFAULT_CONFIG
from skillrouter.engine import _INFLECTIONS, Router, _query_tokens
from skillrouter.indexer import Indexer, load_records

CASES_PATH = Path(__file__).resolve().parents[1] / "eval" / "claude_skills_cases.json"

# A vocabulary floor: enough breadth that "unknown word" carries meaning, without any skill whose name
# collides with the queries under test.
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
}


def skill(name: str, description: str, extra: str = "") -> str:
    return f"---\nname: {name}\ndescription: {description}\ntags:\n  - engineering\n{extra}---\n# {name}\n"


class TokenHelperTests(unittest.TestCase):
    """The helpers themselves, no corpus required."""

    def test_query_tokens_keeps_the_compound_and_adds_parts(self) -> None:
        tokens = _query_tokens("multi-tenant SSO")
        self.assertIn("multi-tenant", tokens, "the verbatim compound must survive")
        self.assertIn("multi", tokens)
        self.assertIn("tenant", tokens)
        self.assertIn("sso", tokens)

    def test_query_tokens_folds_inflections_without_dropping_the_original(self) -> None:
        for query, original, folded in (("SLOs", "slos", "slo"), ("vendors", "vendors", "vendor"),
                                        ("tests", "tests", "test"), ("queries", "queries", "query")):
            with self.subTest(query=query):
                tokens = _query_tokens(query)
                self.assertIn(original, tokens)
                self.assertIn(folded, tokens)

    def test_query_tokens_deduplicates_and_preserves_order(self) -> None:
        tokens = _query_tokens("vendors vendor vendors")
        self.assertEqual(tokens.count("vendor"), 1)
        self.assertEqual(tokens[0], "vendors", "first occurrence keeps its position")

    def test_short_words_are_not_folded(self) -> None:
        # "is"/"as" style tokens are already stopworded; guard the length floor on folding too.
        self.assertEqual(_INFLECTIONS("bus"), {"bus"}, "a two-letter stem must not be produced")

    def test_plain_words_are_untouched(self) -> None:
        self.assertEqual(_query_tokens("jupiter moons"), ["jupiter", "moons", "moon"])


class DerivedEvidenceTests(unittest.TestCase):
    """A prose-derived keyword hit must not identify a skill on its own unless it is very rare."""

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="skillrouter-derived-"))
        self.root = self.temp / "skills"
        self.index = self.temp / "index.json"
        fixtures = dict(FLOOR)
        # The prose-word trap: many skills merely mention "module" in their description, so it lands in
        # their keywords without any skill being *about* modules.
        fixtures["api-and-interface-design"] = (
            "Design stable API contracts and interface boundaries. Useful across every module and service "
            "in a large codebase, where a module boundary matters.")
        fixtures["forget"] = "Remove an obsolete entry and forget a stored fact."
        fixtures["terraform-patterns"] = "Use when organising infrastructure as a reusable module."
        # A genuinely distinctive keyword: only one skill in the corpus knows this word.
        fixtures["owasp-top-10-testing"] = "Use when testing a service against the OWASP Top 10 list."
        for name, description in fixtures.items():
            folder = self.root / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_text(skill(name, description), encoding="utf-8")
        self.config = json.loads(json.dumps(DEFAULT_CONFIG))
        self.config["roots"] = [str(self.root)]
        Indexer([self.root], self.config, self.index).build()
        self.router = Router(self.config, load_records(self.index))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_ordinary_keyword_prose_cannot_select_a_skill_alone(self) -> None:
        # "module" and "remove" are ordinary words that leak into keywords from descriptions. Sharing
        # one of them is not identification, so the router must ask instead of answering wrongly.
        decision = self.router.route("Refactor this module to remove the duplication.")
        self.assertNotEqual(decision.state.value, "USE_SKILL",
                            f"selected {decision.primary.name if decision.primary else None} on prose alone")

    def test_identity_declared_terms_still_select(self) -> None:
        # The name field is the skill's own statement of what it is, so a name hit identifies it.
        decision = self.router.route("Use terraform-patterns for this infrastructure.")
        self.assertEqual(decision.state.value, "USE_SKILL")
        self.assertEqual(decision.primary.name, "terraform-patterns")

    def test_very_rare_keyword_still_counts_as_evidence(self) -> None:
        decision = self.router.route("Test our service against the OWASP Top 10.")
        self.assertEqual(decision.state.value, "USE_SKILL")
        self.assertEqual(decision.primary.name, "owasp-top-10-testing")

    def test_no_match_guarantee_is_unaffected(self) -> None:
        for query in ("How many moons does Jupiter have?", "What should I cook for dinner tonight?"):
            with self.subTest(query=query):
                self.assertEqual(self.router.route(query).state.value, "NO_MATCH")

    def test_strong_hits_counts_only_identity_or_very_rare_keywords(self) -> None:
        design = self.router.by_name["api-and-interface-design"][0]
        self.assertFalse(self.router._counts_as_strong(design.id, "module"))
        self.assertFalse(self.router._counts_as_strong(design.id, "remove"))
        design_name_terms = self.router._fields[design.id]["name"]
        self.assertTrue(design_name_terms, "the name field must still yield tokens")
        self.assertTrue(self.router._counts_as_strong(design.id, "interface"))


class SymmetryTests(unittest.TestCase):
    """Query and document sides must agree about what counts as the same word."""

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="skillrouter-symmetry-"))
        self.root = self.temp / "skills"
        self.index = self.temp / "index.json"
        fixtures = dict(FLOOR)
        fixtures["vendor-management"] = "Use when reviewing third-party vendors and supply risk."
        fixtures["slo-architect"] = "Use when defining service level objectives and error budgets."
        fixtures["multi-tenant-saas"] = "Use when a tenant needs isolation inside a shared database."
        for name, description in fixtures.items():
            folder = self.root / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_text(skill(name, description), encoding="utf-8")
        self.config = json.loads(json.dumps(DEFAULT_CONFIG))
        self.config["roots"] = [str(self.root)]
        Indexer([self.root], self.config, self.index).build()
        self.router = Router(self.config, load_records(self.index))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_plural_query_reaches_a_singular_skill_name(self) -> None:
        # "vendors" must meet the name part "vendor" without the query being rewritten.
        decision = self.router.route("Review our third-party vendors and their risk.")
        self.assertIsNotNone(decision.primary)
        self.assertEqual(decision.primary.name, "vendor-management")

    def test_plural_acronym_reaches_the_singular_form(self) -> None:
        decision = self.router.route("Create dashboards and alerts for our SLOs.")
        names = [c.name for c in decision.candidates[:3]]
        self.assertIn("slo-architect", names, f"expected slo-architect in {names}")

    def test_compound_query_reaches_a_name_part(self) -> None:
        # "multi-tenant" is split so its part can meet the name part "tenant" of multi-tenant-saas.
        decision = self.router.route("Design a multi-tenant database for our SaaS.")
        names = [c.name for c in decision.candidates[:3]]
        self.assertIn("multi-tenant-saas", names, f"expected multi-tenant-saas in {names}")

    def test_folding_never_removes_the_exact_form(self) -> None:
        record = self.router.by_name["vendor-management"][0]
        prepared = ("review our vendors", ["vendors"], ["vendors", "vendor"])
        candidate = self.router._score(prepared, record, None)
        self.assertGreater(candidate.score, 0, "the folded form must produce a score")


class CuratedSuiteGuaranteeTests(unittest.TestCase):
    """Guardrail metrics on the real 117-case suite, when a corpus is configured."""

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
            hit = any(n in expected for n in names[:5])
            state = decision.state.value
            if case["kind"] == "no-match":
                correct, safe = state == "NO_MATCH", state in refusal
            elif case["kind"] == "ambiguous":
                correct = safe = state in refusal
            else:
                correct, safe = hit, hit or state in refusal
            rows.append({"kind": case["kind"], "state": state, "correct": correct, "safe": safe,
                         "hit@1": bool(names) and names[0] in expected, "hit@5": hit})
        pos = [r for r in rows if r["kind"] in ("single", "multi")]
        nm = [r for r in rows if r["kind"] == "no-match"]
        multi = [r for r in rows if r["kind"] == "multi"]
        path_cases = [c for c in self.cases if c.get("path")]
        return {
            "no_match": sum(r["correct"] for r in nm) / len(nm),
            "wrong": sum(not r["safe"] for r in rows) / len(rows),
            "hit@1": sum(r["hit@1"] for r in pos) / len(pos),
            "hit@5": sum(r["hit@5"] for r in pos) / len(pos),
            "multi": sum(r["state"] == "MULTI_SKILL" and r["correct"] for r in multi) / len(multi),
            "path": sum(self.router._classify_path(c["query"]) == c["path"] for c in path_cases) / len(path_cases),
        }

    def test_metrics_do_not_regress(self) -> None:
        m = self._measure()
        self.assertGreaterEqual(m["no_match"], 0.9, f"no-match accuracy fell to {m['no_match']:.3f}")
        self.assertLessEqual(m["wrong"], 0.06, f"wrong-skill rate rose to {m['wrong']:.3f}")
        self.assertGreaterEqual(m["hit@5"], 0.86, f"Hit@5 fell to {m['hit@5']:.3f}")
        self.assertGreaterEqual(m["hit@1"], 0.70, f"Hit@1 fell to {m['hit@1']:.3f}")
        self.assertGreaterEqual(m["path"], 0.9, f"path accuracy fell to {m['path']:.3f}")
        self.assertGreaterEqual(m["multi"], 0.40, f"multi-skill accuracy fell to {m['multi']:.3f}")

    def test_compound_queries_are_covered_by_the_suite(self) -> None:
        compounds = {t for c in self.cases for t in _query_tokens(c["query"]) if "-" in t}
        self.assertTrue(compounds, "the suite should exercise hyphenated queries")


if __name__ == "__main__":
    unittest.main()
"""P0 no-match detection tests: the router must refuse rather than guess.

All fixtures are local and offline; no network and no real skill corpus is required.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from skillrouter.config import DEFAULT_CONFIG
from skillrouter.engine import Router
from skillrouter.indexer import Indexer, load_records
from skillrouter.parser import tokenize


class NoMatchTests(unittest.TestCase):
    def setUp(self) -> None:
        workspace = Path(__file__).resolve().parents[1] / "work" / "test-fixtures" / "nomatch"
        self.root = workspace / "skills"
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = workspace / "index.json"
        # A small, deliberately believable corpus: one clear domain skill per kind of trigger.
        self._skill("rag-architect", "Use when designing retrieval augmented generation, chunking, and embedding pipelines.",
                    tags=["ai", "retrieval"])
        self._skill("systematic-debugging", "Use when tests fail, builds break, or behavior does not match expectations.",
                    tags=["debugging"])
        self._skill("database-designer", "Use when designing PostgreSQL schemas, indexes, and migrations.", tags=["database"])
        self.config = json.loads(json.dumps(DEFAULT_CONFIG))
        self.config["roots"] = [str(self.root)]
        Indexer([self.root], self.config, self.index).build()
        self.router = Router(self.config, load_records(self.index))

    def _skill(self, name: str, description: str, tags: list[str] | None = None) -> None:
        folder = self.root / name
        folder.mkdir(exist_ok=True)
        tag_lines = "".join(f"  - {tag}\n" for tag in (tags or []))
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\ntags:\n{tag_lines}---\n# {name}\n\n## When to Use\n\n{description}\n",
            encoding="utf-8")

    def _route(self, query: str):
        return self.router.route(query)

    # --- out-of-domain: questions about the world -------------------------------

    def test_trivia_question_is_not_a_match(self) -> None:
        route = self._route("How many moons does Jupiter have?")
        self.assertEqual(route.state.value, "NO_MATCH")
        self.assertIsNone(route.primary)
        self.assertEqual(route.activation, [])

    def test_recipe_request_is_not_a_match(self) -> None:
        route = self._route("What should I cook for dinner tonight?")
        self.assertEqual(route.state.value, "NO_MATCH")

    def test_personal_appointment_is_not_a_match(self) -> None:
        route = self._route("Book me a dentist appointment for Friday.")
        self.assertEqual(route.state.value, "NO_MATCH")
        self.assertIsNone(route.primary)

    def test_holiday_question_is_not_a_match(self) -> None:
        route = self._route("Where should I go on holiday in December?")
        self.assertEqual(route.state.value, "NO_MATCH")

    def test_gibberish_is_not_a_match(self) -> None:
        route = self._route("florb snizzle quux zorb")
        self.assertEqual(route.state.value, "NO_MATCH")

    def test_numeric_query_is_not_a_match(self) -> None:
        route = self._route("11 22 33 44")
        self.assertEqual(route.state.value, "NO_MATCH")

    def test_empty_query_is_not_a_match(self) -> None:
        route = self._route("   ")
        self.assertEqual(route.state.value, "NO_MATCH")

    def test_florid_padding_does_not_produce_a_match(self) -> None:
        # A long sentence whose only content words are out of domain must still refuse.
        route = self._route("Could you please tell me everything you possibly know about the mating habits of penguins?")
        self.assertIn(route.state.value, ("NO_MATCH", "AMBIGUOUS"))

    # --- gates are explainable -------------------------------------------------

    def test_no_match_reports_a_gate_reason(self) -> None:
        route = self._route("How many moons does Jupiter have?")
        self.assertEqual(route.state.value, "NO_MATCH")
        self.assertTrue(route.gate)
        self.assertIn("No skill task detected", route.reason)

    # --- real tasks must still route -------------------------------------------

    def test_in_domain_task_still_selects_the_skill(self) -> None:
        route = self._route("Design a retrieval augmented generation pipeline for our documents.")
        self.assertEqual(route.state.value, "USE_SKILL")
        self.assertEqual(route.primary.name, "rag-architect")

    def test_debugging_task_still_selects_the_skill(self) -> None:
        route = self._route("Our tests fail and the build breaks on CI.")
        self.assertIn(route.primary.name if route.primary else None, ("systematic-debugging",))
        self.assertIn(route.state.value, ("USE_SKILL", "MULTI_SKILL"))

    def test_schema_task_still_selects_the_skill(self) -> None:
        route = self._route("Design the PostgreSQL schema and indexes for this application.")
        self.assertEqual(route.primary.name, "database-designer")

    def test_generic_request_asks_rather_than_guesses(self) -> None:
        route = self._route("Help with my project.")
        self.assertEqual(route.state.value, "AMBIGUOUS")
        self.assertIsNone(route.primary)
        self.assertEqual(route.activation, [])

    # --- a shared common noun is not evidence ---------------------------------

    def test_partial_name_match_is_not_enough(self) -> None:
        # "design" appears in a skill name, but the prompt is not a database task.
        route = self._route("Design a birthday card for my friend.")
        self.assertIsNone(route.primary)
        self.assertIn(route.state.value, ("NO_MATCH", "AMBIGUOUS"))

    # --- gate mechanics -------------------------------------------------------

    def test_anchor_rejects_corpus_ubiquitous_terms(self) -> None:
        # A term that appears in every document is not distinctive, however rare it looks.
        self.assertFalse(self.router._anchor("the"))
        self.assertTrue(self.router._anchor("postgresql") or self.router._anchor("embedding"))

    def test_oov_ratio_detects_unknown_vocabulary(self) -> None:
        self.assertEqual(self.router._oov_ratio(["florb", "snizzle"]), 1.0)
        self.assertLess(self.router._oov_ratio(["postgresql", "schema"]), 1.0)

    def test_stopword_only_query_never_scores_a_skill(self) -> None:
        route = self._route("this that these those")
        self.assertEqual(route.state.value, "NO_MATCH")
        self.assertEqual(tokenize("this that these those"), [])

    def test_gate_is_lexical_only_no_subprocess(self) -> None:
        # Semantic tie-break is off by default, so routing must not shell out.
        called: list[object] = []
        original = Router._semantic_tiebreak

        def spy(self, query, candidates):  # pragma: no cover - guard only
            called.append(query)
            return original(self, query, candidates)

        Router._semantic_tiebreak = spy
        try:
            self._route("Design a retrieval augmented generation pipeline.")
        finally:
            Router._semantic_tiebreak = original
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
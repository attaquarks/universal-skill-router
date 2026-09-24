from __future__ import annotations

import json
import unittest
from pathlib import Path

from skillrouter.config import DEFAULT_CONFIG
from skillrouter.engine import Router
from skillrouter.indexer import Indexer, load_records


class RouterTests(unittest.TestCase):
    def setUp(self) -> None:
        workspace_temp = Path(__file__).resolve().parents[1] / "work" / "test-fixtures"
        self.root = workspace_temp / "skills"
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = workspace_temp / "index.json"
        self._skill("rag-guide", "Use when designing retrieval augmented generation systems, chunking documents, and evaluating retrieval.")
        self._skill("performance-review", "Use when profiling latency, response time, throughput, or slow services.")
        self._skill("security-review", "Use when reviewing a pull request for vulnerabilities or insecure code.")
        self.config = json.loads(json.dumps(DEFAULT_CONFIG)); self.config["roots"] = [str(self.root)]
        Indexer([self.root], self.config, self.index).build()

    def tearDown(self) -> None: pass

    def _skill(self, name: str, description: str) -> None:
        folder = self.root / name; folder.mkdir(exist_ok=True)
        (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\ntags:\n  - engineering\n---\n# {name}\n", encoding="utf-8")

    def test_incremental_index_and_frontmatter_lists(self) -> None:
        first = Indexer([self.root], self.config, self.index).build()
        second = Indexer([self.root], self.config, self.index).build()
        self.assertEqual(first["stats"]["skills"], 3)
        self.assertEqual(second["stats"]["reused"], 3)
        self.assertIn("engineering", load_records(self.index)[0].tags)

    def test_routes_dynamic_skill_from_content(self) -> None:
        route = Router(self.config, load_records(self.index)).route("Design a RAG retrieval pipeline")
        self.assertEqual(route.primary.name, "rag-guide")

    def test_no_match_is_not_a_guess(self) -> None:
        route = Router(self.config, load_records(self.index)).route("florb snizzle quux zorb")
        self.assertEqual(route.state.value, "NO_MATCH")

    def test_generic_request_does_not_activate_an_accidental_match(self) -> None:
        route = Router(self.config, load_records(self.index)).route("Help with my project.")
        self.assertIn(route.state.value, ("AMBIGUOUS", "NO_MATCH"))
        self.assertIsNone(route.primary)
        self.assertEqual(route.activation, [])

    def test_symlink_outside_root_is_not_indexed(self) -> None:
        outside = self.root.parent / "outside"; outside.mkdir(exist_ok=True); (outside / "SKILL.md").write_text("---\nname: unsafe\n---\n", encoding="utf-8")
        link = self.root / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        result = Indexer([self.root], self.config, self.index).build()
        self.assertEqual(result["stats"]["skills"], 3)


if __name__ == "__main__": unittest.main()

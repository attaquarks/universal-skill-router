"""Evaluate an indexed router against a transparent local fixture; no claims are hard-coded."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skillrouter.config import load_config
from skillrouter.engine import Router


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="eval/claude_skills_cases.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--config")
    args = parser.parse_args()
    router = Router(load_config(Path(args.config) if args.config else None) if args.config else load_config())
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    results = []
    for case in cases:
        route = router.route(case["query"])
        names = [candidate.name for candidate in route.candidates]
        expected = set(case["expected_any"])
        hit_rank = next((index + 1 for index, name in enumerate(names) if name in expected), None)
        kind = case["kind"]
        expected_no_match = kind == "no-match"
        expected_ambiguous = kind == "ambiguous"
        if expected_no_match:
            correct = route.state.value == "NO_MATCH"
        elif expected_ambiguous:
            correct = route.state.value == "AMBIGUOUS"
        else:
            correct = hit_rank is not None
        results.append({"id": case["id"], "input": case["query"], "expected_any": case["expected_any"], "candidates": names,
            "selected": [route.primary.name] if route.primary else [], "supporting": [candidate.name for candidate in route.supporting],
            "state": route.state.value, "confidence": route.confidence, "reason": route.reason, "latency_ms": route.latency_ms,
            "hit_rank": hit_rank, "kind": kind, "correct": correct})
    regular = [result for result in results if result["kind"] in ("single", "multi")]
    no_match = [result for result in results if result["kind"] == "no-match"]
    ambiguous = [result for result in results if result["kind"] == "ambiguous"]
    summary = {"case_count": len(results), "positive_cases": len(regular), "Hit@1": round(sum(result["hit_rank"] == 1 for result in regular) / max(1, len(regular)), 3),
        "Hit@3": round(sum(bool(result["hit_rank"] and result["hit_rank"] <= 3) for result in regular) / max(1, len(regular)), 3),
        "Hit@5": round(sum(bool(result["hit_rank"] and result["hit_rank"] <= 5) for result in regular) / max(1, len(regular)), 3),
        "no_match_accuracy": round(sum(result["correct"] for result in no_match) / max(1, len(no_match)), 3),
        "ambiguity_accuracy": round(sum(result["correct"] for result in ambiguous) / max(1, len(ambiguous)), 3),
        "multi_skill_state_accuracy": round(sum(result["state"] == "MULTI_SKILL" for result in regular if result["kind"] == "multi") / max(1, sum(result["kind"] == "multi" for result in regular)), 3),
        "latency_ms": {"median": round(statistics.median(result["latency_ms"] for result in results), 2), "max": round(max(result["latency_ms"] for result in results), 2)}}
    payload = {"suite": "installed-skills-fixture", "corpus": {"indexed_skill_count": len(router.records),
        "root_count": len({record.root for record in router.records}), "upstream_provenance_verified": False},
        "evaluation_limits": "Expected skill sets are manually curated, broad relevance labels, not blinded human judgments; this is a smoke benchmark, not a general accuracy claim.",
        "summary": summary, "results": results}
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())

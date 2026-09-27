"""Evaluate an indexed router against a transparent local fixture; no claims are hard-coded.

Reports overall state accuracy, retrieval hit rates, latency, and per-path (BROKEN/BUILD/OPERATE)
breakdowns. The corpus is whatever is indexed locally; numbers are real for that corpus only.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skillrouter.config import load_config
from skillrouter.engine import Router

POSITIVE_KINDS = ("single", "multi")
REFUSAL_STATES = ("AMBIGUOUS", "NO_MATCH")


def _rate(values: list[bool]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


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
        if kind == "no-match":
            # NO_MATCH is the right answer; AMBIGUOUS (ask a question) is a safe miss, not a wrong skill.
            correct = route.state.value == "NO_MATCH"
            safe = route.state.value in REFUSAL_STATES
        elif kind == "ambiguous":
            correct = safe = route.state.value in REFUSAL_STATES
        else:
            correct = hit_rank is not None
            safe = correct or route.state.value in REFUSAL_STATES
        results.append({"id": case["id"], "input": case["query"], "expected_any": case["expected_any"], "candidates": names,
            "selected": [route.primary.name] if route.primary else [], "supporting": [candidate.name for candidate in route.supporting],
            "state": route.state.value, "confidence": route.confidence, "path": route.path, "gate": route.gate,
            "reason": route.reason, "latency_ms": route.latency_ms, "hit_rank": hit_rank, "kind": kind,
            "expected_path": case.get("path"), "correct": correct, "safe": safe})
    regular = [result for result in results if result["kind"] in POSITIVE_KINDS]
    no_match = [result for result in results if result["kind"] == "no-match"]
    ambiguous = [result for result in results if result["kind"] == "ambiguous"]
    per_path = {}
    for label in ("BROKEN", "BUILD", "OPERATE"):
        subset = [result for result in results if result.get("expected_path") == label]
        if subset:
            per_path[label] = {"cases": len(subset),
                "retrieval_accuracy": _rate([result["correct"] for result in subset]),
                "path_accuracy": _rate([result["path"] == result["expected_path"] for result in subset])}
    latencies = sorted(result["latency_ms"] for result in results)
    summary = {"case_count": len(results), "positive_cases": len(regular),
        "Hit@1": _rate([result["hit_rank"] == 1 for result in regular]),
        "Hit@3": _rate([bool(result["hit_rank"] and result["hit_rank"] <= 3) for result in regular]),
        "Hit@5": _rate([bool(result["hit_rank"] and result["hit_rank"] <= 5) for result in regular]),
        "no_match_accuracy": _rate([result["correct"] for result in no_match]),
        "no_match_safe_rate": _rate([result["safe"] for result in no_match]),
        "ambiguity_accuracy": _rate([result["correct"] for result in ambiguous]),
        "multi_skill_state_accuracy": _rate([result["state"] == "MULTI_SKILL" for result in regular if result["kind"] == "multi"]),
        "wrong_skill_rate": _rate([not result["safe"] for result in results]),
        "path_accuracy": _rate([result["path"] == result["expected_path"] for result in results if result.get("expected_path")]),
        "per_path": per_path,
        "latency_ms": {"median": round(statistics.median(latencies), 2), "p95": round(latencies[int(0.95 * (len(latencies) - 1))], 2),
            "max": round(max(latencies), 2)}}
    payload = {"suite": "installed-skills-fixture", "corpus": {"indexed_skill_count": len(router.records),
        "root_count": len({record.root for record in router.records}), "upstream_provenance_verified": False},
        "evaluation_limits": "Expected skill sets are manually curated, broad relevance labels, not blinded human judgments; this is a smoke benchmark, not a general accuracy claim. The corpus is the locally indexed skill directory, so the numbers are real for this machine only and are not reproducible from a fresh clone.",
        "summary": summary, "results": results}
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())

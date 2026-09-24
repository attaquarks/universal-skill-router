from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .config import EVENTS_PATH, LEARNED_PATH, atomic_json
from .parser import tokenize


def record_event(event: dict[str, Any], config: dict[str, Any], path: Path = EVENTS_PATH) -> None:
    if not config.get("learning", {}).get("enabled", True):
        return
    event = dict(event)
    event["timestamp"] = time.time()
    prompt = str(event.pop("prompt", ""))
    if prompt:
        event["prompt_hash"] = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        event["tokens"] = tokenize(prompt)[:24]
        if config.get("learning", {}).get("store_raw_prompts", False):
            event["prompt"] = prompt
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def learn(config: dict[str, Any], events_path: Path = EVENTS_PATH, output_path: Path = LEARNED_PATH) -> dict[str, Any]:
    if not config.get("learning", {}).get("enabled", True):
        return {"enabled": False, "events": 0}
    events: list[dict[str, Any]] = []
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict): events.append(value)
            except json.JSONDecodeError:
                continue
    recommended, loaded, ignored, overrides = Counter(), Counter(), Counter(), Counter()
    token_skill: dict[str, Counter[str]] = defaultdict(Counter)
    transitions: Counter[tuple[str, str]] = Counter()
    last: str | None = None
    for event in events:
        skill = str(event.get("skill", ""))
        kind = event.get("event")
        if kind == "recommended" and skill: recommended[skill] += 1
        elif kind == "loaded" and skill:
            loaded[skill] += 1
            for token in event.get("tokens", []): token_skill[str(token)][skill] += 1
            if last and last != skill: transitions[(last, skill)] += 1
            last = skill
        elif kind == "ignored" and skill: ignored[skill] += 1
        elif kind == "override" and skill: overrides[skill] += 1
    minimum = int(config.get("learning", {}).get("min_events", 3))
    boosts = {token: {skill: round(min(0.5, count / max(1, loaded[skill]) * 0.5), 3) for skill, count in matches.items() if count >= minimum}
              for token, matches in token_skill.items()}
    boosts = {token: matches for token, matches in boosts.items() if matches}
    payload = {"version": 1, "generated_at": time.time(), "event_count": len(events), "token_skill_boosts": boosts,
               "skill_outcomes": {skill: {"recommended": recommended[skill], "loaded": loaded[skill], "ignored": ignored[skill], "overrides": overrides[skill],
                   "follow_rate": round(loaded[skill] / recommended[skill], 3) if recommended[skill] else 0} for skill in set(recommended) | set(loaded) | set(ignored) | set(overrides)},
               "handoffs": [{"from": first, "to": second, "count": count} for (first, second), count in transitions.most_common(50)]}
    atomic_json(output_path, payload)
    return payload

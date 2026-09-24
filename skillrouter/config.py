from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

APP_DIR = Path(os.environ.get("SKILLROUTER_HOME", Path.home() / ".skillrouter"))
CONFIG_PATH = APP_DIR / "config.json"
INDEX_PATH = APP_DIR / "index.json"
EVENTS_PATH = APP_DIR / "events.jsonl"
LEARNED_PATH = APP_DIR / "learned.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1, "roots": [], "max_skill_bytes": 1_000_000, "max_excerpt_chars": 8_000,
    "follow_symlinks": False,
    "confidence": {"use": 0.62, "support": 0.47, "margin": 0.05, "no_match": 0.28},
    "routing": {"max_candidates": 5, "max_chain_skills": 3, "prefer_orchestrators": True},
    "enforcement": {"mode": "ADVISORY"},
    "learning": {"enabled": True, "store_raw_prompts": False, "min_events": 3},
    "semantic": {"enabled": False, "command": "", "timeout_seconds": 5},
    "projects": [], "adapters": {},
}


def default_roots() -> list[str]:
    """Conservative defaults; explicit config remains the authoritative scan scope."""
    home = Path.home()
    candidates = [home / ".agents" / "skills", home / ".claude" / "skills", home / ".codex" / "skills"]
    return [str(path) for path in candidates if path.is_dir()]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        result[key] = _deep_merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Cannot read configuration {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Configuration must be a JSON object")
    return _deep_merge(DEFAULT_CONFIG, payload)


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def save_config(config: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    atomic_json(path, config)

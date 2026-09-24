from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

from .config import INDEX_PATH, atomic_json
from .models import SkillRecord, under_root
from .parser import parse_skill

INDEX_VERSION = 2


class Indexer:
    """Safe, incremental scanner. It reads SKILL.md files and never runs their code."""

    def __init__(self, roots: Iterable[str | Path], config: dict[str, Any], index_path: Path = INDEX_PATH):
        self.roots = [Path(root).expanduser() for root in roots]
        self.config = config
        self.index_path = index_path

    def _safe_roots(self) -> list[Path]:
        result: list[Path] = []
        for root in self.roots:
            try:
                resolved = root.resolve(strict=True)
            except OSError:
                continue
            if not resolved.is_dir():
                continue
            if resolved not in result:
                result.append(resolved)
        return result

    def _files(self, root: Path) -> Iterable[Path]:
        follow = bool(self.config.get("follow_symlinks", False))
        for directory, dirnames, filenames in os.walk(root, followlinks=follow):
            current = Path(directory)
            if not follow:
                dirnames[:] = [name for name in dirnames if not (current / name).is_symlink()]
            if "SKILL.md" not in filenames:
                continue
            candidate = current / "SKILL.md"
            try:
                resolved = candidate.resolve(strict=True)
                if under_root(resolved, root) and resolved.is_file():
                    yield resolved
            except OSError:
                continue

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(131072), b""):
                digest.update(block)
        return digest.hexdigest()

    def load(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"version": INDEX_VERSION, "skills": []}
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": INDEX_VERSION, "skills": []}
        return payload if payload.get("version") == INDEX_VERSION else {"version": INDEX_VERSION, "skills": []}

    def build(self) -> dict[str, Any]:
        started = time.perf_counter()
        prior = self.load()
        prior_by_path = {item["path"]: item for item in prior.get("skills", []) if isinstance(item, dict) and item.get("path")}
        records: list[dict[str, Any]] = []
        scanned = reused = skipped = 0
        max_bytes = int(self.config.get("max_skill_bytes", 1_000_000))
        for root in self._safe_roots():
            for path in self._files(root):
                try:
                    stat = path.stat()
                    if stat.st_size > max_bytes:
                        skipped += 1
                        continue
                    key = str(path)
                    prior_item = prior_by_path.get(key)
                    if prior_item and prior_item.get("mtime_ns") == stat.st_mtime_ns and prior_item.get("size") == stat.st_size:
                        records.append(prior_item)
                        reused += 1
                        continue
                    digest = self._digest(path)
                    if prior_item and prior_item.get("source_hash") == digest:
                        prior_item["mtime_ns"] = stat.st_mtime_ns
                        prior_item["size"] = stat.st_size
                        records.append(prior_item)
                        reused += 1
                        continue
                    record = parse_skill(path, root, digest, stat.st_mtime_ns, stat.st_size, int(self.config.get("max_excerpt_chars", 8000)))
                    records.append(record.to_dict())
                    scanned += 1
                except (OSError, UnicodeError, ValueError):
                    skipped += 1
        payload = {"version": INDEX_VERSION, "indexed_at": time.time(), "roots": [str(root) for root in self._safe_roots()],
                   "skills": sorted(records, key=lambda skill: (skill["name"].lower(), skill["path"])),
                   "stats": {"skills": len(records), "scanned": scanned, "reused": reused, "skipped": skipped,
                             "latency_ms": round((time.perf_counter() - started) * 1000, 2)}}
        atomic_json(self.index_path, payload)
        return payload


def load_records(index_path: Path = INDEX_PATH) -> list[SkillRecord]:
    if not index_path.exists():
        return []
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    return [SkillRecord.from_dict(item) for item in payload.get("skills", [])]

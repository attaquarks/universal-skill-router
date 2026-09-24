from __future__ import annotations

import re
from pathlib import Path

from .models import SkillRecord

FRONTMATTER_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|\Z)", re.DOTALL)
HEADING_RE = re.compile(r"^#{1,4}\s+(.+?)\s*$", re.MULTILINE)
TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_+.#/-]{1,}")
STOPWORDS = frozenset("a an and are as at be by for from how in into is it of on or our that the this to use when with your you i me my we they he she what who whom whose why which should would could can do does did have has had will won".split())


def tokenize(text: str) -> list[str]:
    return [t.lower().strip("-_/.") for t in TOKEN_RE.findall(text) if t.lower() not in STOPWORDS and len(t) > 1]


def _scalar(value: str) -> str:
    value = value.strip()
    if " #" in value and not value.startswith(("'", '"')):
        value = value.split(" #", 1)[0].rstrip()
    return value[1:-1].replace("\\\"", "\"") if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value


def parse_frontmatter(raw: str) -> tuple[dict[str, object], list[str], str]:
    match = FRONTMATTER_RE.match(raw)
    if not match:
        return {}, ["missing YAML frontmatter"], raw
    values: dict[str, object] = {}
    warnings: list[str] = []
    current_list: str | None = None
    nested: str | None = None
    for number, line in enumerate(match.group(1).splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            stripped = line.strip()
            if current_list and stripped.startswith("- "):
                values.setdefault(current_list, []).append(_scalar(stripped[2:]))  # type: ignore[union-attr]
            elif nested and ":" in stripped:
                key, value = stripped.split(":", 1)
                values[f"{nested}.{key.strip()}"] = _scalar(value)
            else:
                warnings.append(f"unparsed frontmatter line {number}")
            continue
        current_list = None
        nested = None
        if ":" not in line:
            warnings.append(f"unparsed frontmatter line {number}")
            continue
        key, value = (item.strip() for item in line.split(":", 1))
        if not value:
            nested = key
            current_list = key
        elif value in ("|", ">"):
            warnings.append(f"block scalar not indexed for {key}")
        elif value.startswith("[") and value.endswith("]"):
            values[key] = [_scalar(v) for v in value[1:-1].split(",") if v.strip()]
        else:
            values[key] = _scalar(value)
    return values, warnings, raw[match.end():]


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [value] if isinstance(value, str) and value else []


def _sentences_after_labels(body: str, labels: tuple[str, ...]) -> list[str]:
    return [line.strip(" -*\t") for line in body.splitlines() if len(line.strip()) < 500 and any(label in line.lower() for label in labels)][:16]


def _positive_text(text: str) -> str:
    """Do not turn a skill's explicit 'NOT for X' disambiguation into a positive match."""
    text = re.sub(r"\b(?:not|never)\s+for\b[^.\n;]*", "", text, flags=re.IGNORECASE)
    return "\n".join(line for line in text.splitlines() if "not for" not in line.lower())


def parse_skill(path: Path, root: Path, source_hash: str, mtime_ns: int, size: int, max_excerpt: int) -> SkillRecord:
    raw = path.read_text(encoding="utf-8", errors="replace")
    frontmatter, warnings, body = parse_frontmatter(raw)
    name = str(frontmatter.get("name") or path.parent.name).strip().strip("\"")
    description = str(frontmatter.get("description") or "")
    aliases = _as_list(frontmatter.get("aliases"))
    tags = _as_list(frontmatter.get("tags")) + _as_list(frontmatter.get("metadata.tags"))
    triggers = _as_list(frontmatter.get("triggers")) + _as_list(frontmatter.get("use_when"))
    domain = str(frontmatter.get("domain") or frontmatter.get("category") or frontmatter.get("metadata.category") or "")
    headings = [heading.strip()[:160] for heading in HEADING_RE.findall(body)[:24]]
    trigger_lines = _sentences_after_labels(body, ("use when", "when to use", "best for", "trigger"))
    triggers.extend(trigger_lines)
    related_lines = _sentences_after_labels(body, ("related skill", "orchestrat", "router"))
    related = [token for line in related_lines for token in re.findall(r"`([^`]+)`", line)]
    keywords = list(dict.fromkeys(tokenize(_positive_text(" ".join([name, description, domain, *aliases, *tags, *triggers, *headings])))))[:120]
    compact_body = _positive_text("\n".join([*headings, *trigger_lines, *related_lines, body[:max_excerpt]]))[:max_excerpt]
    lower_body = body.lower()
    is_orchestrator = frontmatter.get("router") is True or frontmatter.get("metadata.router") == "true"
    is_orchestrator = is_orchestrator or ("orchestrat" in lower_body and "skill" in lower_body and ("chain" in lower_body or "handoff" in lower_body))
    return SkillRecord(id=str(path.resolve()), name=name, path=str(path.resolve()), root=str(root.resolve()), source_hash=source_hash,
        mtime_ns=mtime_ns, size=size, description=description, domain=domain, aliases=aliases, tags=list(dict.fromkeys(tags)),
        triggers=list(dict.fromkeys(triggers)), keywords=keywords, related_skills=list(dict.fromkeys(related)), headings=headings,
        body_excerpt=compact_body, is_orchestrator=is_orchestrator, warnings=warnings)

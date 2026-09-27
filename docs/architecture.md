# Architecture

## Purpose

The router sits above Agent Skills. The library owns skill instructions, optional scripts, references, templates, agents, and domain orchestration. The router discovers and ranks existing `SKILL.md` files, then gives the host a bounded activation decision.

## Pipeline

1. **Discover:** recursively locate `SKILL.md` beneath explicit roots, avoid symlink traversal by default, enforce path containment and file-size bounds.
2. **Index:** parse common scalar/list YAML frontmatter plus skill headings and bounded trigger/use-when excerpts. Store SHA-256, stat data, metadata, and excerpt. Reuse unchanged rows.
3. **Retrieve:** weighted BM25-style scores across name, triggers, aliases, keywords, description, domain, and excerpt. Apply exact names, phrase aliases, project preferences, and modest learned token boosts.
4. **Decide:** compare score strength and lead margin. Return `USE_SKILL`, `MULTI_SKILL`, `AMBIGUOUS`, or `NO_MATCH`, with ranked evidence and explanation.
5. **Activate:** adapter maps selected file paths to host instructions. Semantic tie breaking is optional and explicit.
6. **Learn:** locally aggregate selected/loaded/ignored/override events and transitions. Raw prompts are omitted unless configured.

## Confidence

The confidence field is a heuristic score combining saturating lexical strength and rank separation. It is not a probability and is not calibrated against human judgments. A high score without distinguishing evidence can still be ambiguous. Evaluate the thresholds against your skill corpus and queries before using AUTO behavior.

## Out-of-domain gate (why NO_MATCH is trustworthy)

A high lexical score is not evidence that a prompt is a skill task, so the router runs a lexical
evidence gate before it will select anything. The gate never changes ranking; it only decides whether
the winner's evidence is real. In order:

1. **Noise** — an empty, fragmented, stopword-only or numeric query is not a task.
2. **Out of domain** — a question shape ("what/where/who/why/how", or a trailing `?`) with no work verb,
   or a question about the world rather than the user's work, is not a skill task.
3. **Corpus context** — if half or more of the query's content terms appear in no indexed skill,
   nothing installed covers it. Inflections are folded ("breaks" matches "break") so ordinary
   phrasing is not mistaken for unknown vocabulary.
4. **Evidence** — the winner must show a skill-name claim (two claimed name terms, one rare claimed
   term, or an exact name/alias phrase) or at least one rare, non-generic term in a *strong* field:
   `name`, `aliases`, `triggers`, `keywords`. Rarity is document frequency in this corpus (≤14% of
   skills, ≤3% considered very rare), not a fixed IDF cutoff, so the gate travels to a corpus of any
   size. Description and body are deliberately excluded: a rare word merely mentioned in prose is not
   evidence of a skill.

Verdicts differ by cause. Input that is not a skill task returns `NO_MATCH`. Input that is plausible
work with no identifiable skill returns `AMBIGUOUS` — the router asks instead of guessing. A query
that shares exactly one common noun with a skill name, with nothing else in common, is `AMBIGUOUS`:
"build a test suite" must not select `api-test-suite-builder`.

## Domain routers and chains

The parser reads optional `metadata.router` or `router` fields and can infer a possible orchestrator from its instructions. Such an entry receives a small preference only after it already matches query evidence. Project routes can name an orchestrator or curated chain. The universal router does not replace or reconstruct domain-level orchestration protocols.

## Scaling

Index work scales with new or modified files plus directory traversal; unchanged files reuse prior rows via stat checks. Query scoring currently scans all in-memory records, so latency grows approximately linearly with skill count. This implementation is suitable for hundreds and should be measured for thousands; a persisted inverted index or SQLite FTS backend is a future optimization for very large catalogs.

## Module boundaries

- `parser.py`: conservative frontmatter and body extraction
- `indexer.py`: bounded discovery and incremental catalog
- `engine.py`: retrieval, confidence, project overlays, activation list
- `learning.py`: local events and aggregates
- `adapters.py`: host capabilities
- `cli.py`: user-facing commands

The portable `meta-skill/SKILL.md` explains how an agent calls the CLI and interprets decisions.

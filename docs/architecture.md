# Architecture

## Purpose

The router sits above Agent Skills. The library owns skill instructions, optional scripts, references, templates, agents, and domain orchestration. The router discovers and ranks existing `SKILL.md` files, then gives the host a bounded activation decision.

## Pipeline

1. **Discover:** recursively locate `SKILL.md` beneath explicit roots, avoid symlink traversal by default, enforce path containment and file-size bounds.
2. **Index:** parse common scalar/list YAML frontmatter plus skill headings and bounded trigger/use-when excerpts. Store SHA-256, stat data, metadata, and excerpt. Reuse unchanged rows.
3. **Retrieve:** weighted BM25-style scores across name, triggers, aliases, keywords, description, domain, and excerpt. Apply exact names, phrase aliases, project preferences, and modest learned token boosts.

### The `usewhen` field

A skill states its activation conditions in its description ("Use when tests fail, builds break…"). The
`usewhen` field holds that applicability text and is scored at weight 2.2, well above `description` at
1.2, so the conditions a skill declares matter more than the prose around them.

It holds the *clause*, not the sentence: the text after the label ("auditing accessibility, fixing
violations, or checking color contrast"), not the label ("Use when") and not the head of the sentence
("Accessibility audit skill for scanning … across React, Next.js and Vue"). The label is boilerplate, and
the head is text the `description` field already scores — keeping either would score the same prose
twice, once at double weight. A colon is deliberately not a sentence boundary, so colon-form labels
(`When to use: …`, `Triggers: …`) keep their clause instead of leaving it orphaned.

Descriptions with no applicability condition yield an empty field rather than a copy of the description,
for the same double-counting reason. Vocabulary coverage is unaffected: document frequency is built from
every field including the description, so a term absent from `usewhen` is still known to the corpus.

The field is a pure function of the description and is always derived at load time, never read back from
the index. An index written by an earlier version therefore picks up the current extraction with no
rebuild, and every install scores identical text for the same `SKILL.md`.
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
   term, or an exact name/alias phrase) or at least one rare, non-generic term in a *strong* field.
   Rarity is document frequency in this corpus (≤14% of skills, ≤3% considered very rare), not a fixed
   IDF cutoff, so the gate travels to a corpus of any size.

   Strong fields are split by provenance, because not every indexed field says the same thing about a
   skill:

   | field | provenance | counts as evidence |
   | --- | --- | --- |
   | `name`, `aliases`, `triggers` | declared by the skill about itself | any rare, non-generic term |
   | `keywords` | derived from prose (`parse_skill` builds it from name + description + domain + tags + headings) | only **very** rare terms (≤3% of skills) |
   | `description`, `body` | prose | never |

   Keywords are derived, not declared: 82% of their tokens come from the description alone. Treating a
   keyword hit like an identity claim let "Refactor this module to remove the duplication." select a
   skill on the single word `module`, which is ordinary prose spread across 19 skills. `owasp` or
   `accessibility` still identify a skill; `module` and `remove` do not. Description and body are
   excluded outright: a rare word merely mentioned in prose is not evidence of a skill.

   Query and document must also agree about what counts as the same word. The name field is
   hyphen-split, so a query does the same: `multi-tenant` yields both the compound (matching keywords
   and descriptions verbatim) and its parts (`multi`, `tenant`), which is the only way to reach a name
   part. Trivial inflections are folded on the query side exactly as the corpus-context check folds
   them, so `vendors` reaches a skill whose own name says `vendor` and `SLOs` reaches `SLO`. Folded and
   split forms are only ever *added*, never substituted, so an exact match still outranks an
   approximate one.

Verdicts differ by cause. Input that is not a skill task returns `NO_MATCH`. Input that is plausible
work with no identifiable skill returns `AMBIGUOUS` — the router asks instead of guessing. A query
that shares exactly one common noun with a skill name, with nothing else in common, is `AMBIGUOUS`:
"build a test suite" must not select `api-test-suite-builder`.

### Path-scoped query expansion

Expansion is context-sensitive in one place. A failure is reported by *symptom* ("the app crashes",
"we have a leak") while the skills that resolve it are *named* with triage vocabulary —
`systematic-debugging`, `debugging-and-error-recovery`, `incident-commander`, `incident-response`. The
topic noun in such a query ("app", "memory", "pool", "queue") is frequently a name token of an unrelated
skill, so it collects the IDF×10 name boost and buries the resolver: "Debug a memory leak in the worker
process" scored four `memory-*` skills at 75–80 while the debugging skills sat at rank 6 and 52.

`SYMPTOM_TRIAGE` bridges symptom onto triage vocabulary, and is applied **only when the query is already
triaged BROKEN**. It is a separate table from `SYNONYMS` rather than an addition to it, because a global
mapping would make "memory" or "queue" mean "failure" in "design a memory pool" — the topic-domination
bug in reverse. Triage therefore runs before query preparation in `route()`, so expansion can see the
path; with `routing.triage = false` there is no path and no scoped expansion.

The table is restricted to symptom *keys*. Topic nouns such as `memory`, `pool` and `queue` are
explicitly excluded: mapping them toward triage leaks debugging skills into build and operate queries.

### Evidence and the scored token set

Name evidence is computed from the same prepared token list that BM25 scores, so the two sides of the
engine always describe the same query. The prepared set is the raw query tokens plus, only ever as
*additions*, their folded forms (`SLOs` → `slos`, `slo`), the parts of any hyphen compound
(`multi-tenant` → `multi`, `tenant`) and the synonym expansions. Additive-only is deliberate: an exact
match must keep outranking an approximate one.

This matters beyond scoring because *signals are evidence*. A skill named `slo-architect` is a distinct
claim only if the query's `SLOs` is recognised as naming it. While the signal path read the raw tokens
the same query scored correctly but produced no signal, so the match could not support a chain and could
be reported as ambiguous instead of selected. It also gives the scoped triage vocabulary a second role:
`debugging`, `failure` and `incident` are name tokens of the resolving skills, so once they are part of
the query's signal set those skills register as a genuine second claim rather than as duplicates of the
primary that merely share its topic.

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

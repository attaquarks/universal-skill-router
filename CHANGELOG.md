# Changelog

## Unreleased

- **Path-scoped symptom expansion.** A failure is reported by symptom while the skills that resolve it are
  named with triage vocabulary (`systematic-debugging`, `debugging-and-error-recovery`,
  `incident-commander`). The topic noun in such a query is often a *name* token of an unrelated skill, so it
  collects the IDF×10 name boost and buries the resolver. `SYMPTOM_TRIAGE` bridges symptom onto triage
  vocabulary and is applied **only when the query is already triaged BROKEN**. Triage now runs before query
  preparation so expansion can see the path, and `routing.triage = false` disables both.
- The table is deliberately separate from `SYNONYMS`, not merged into it: applied globally it would make
  "memory" or "queue" mean "failure" in a build or operate query about those things. Topic nouns (`memory`,
  `pool`, `queue`, `app`) are excluded as keys for the same reason, and verified not to leak on
  BUILD/OPERATE topic queries.
- BROKEN retrieval 0.733 → 0.933, Hit@5 0.919 → 0.953, Hit@1 0.756 → 0.779, positive selection
  0.814 → 0.837. Wrong-skill rate **improved** 0.034 → 0.026 — `pool-exhausted` stopped being a wrong
  answer. no-match accuracy (0.909), no-match safety (1.0), ambiguity accuracy (1.0) and path accuracy
  (0.942) unchanged. Median latency 19.2 → 19.7 ms (interleaved, 6 passes).

- **`usewhen` holds the applicability clause.** The field is scored at 2.2 — the router's strongest prose
  signal — but it was holding whole sentences, so for the 389 descriptions whose label begins the sentence
  it scored the same prose the 1.2 description field already scored. It now holds the text after the label
  and nothing else: no "Use when" boilerplate, no sentence head. Colon-form labels (`When to use: …`,
  `Triggers: …`) keep their clause, because a colon is no longer treated as a sentence boundary. Records
  whose field still carried label boilerplate: 61 → 5.
- **No fallback copy.** A description with no applicability condition yields an empty field instead of a
  verbatim copy of the description, which would have scored the same text at both 2.2 and 1.2. Vocabulary
  coverage is unaffected: document frequency spans every field, description included.
- **Always derived, never read back.** `usewhen` is a pure function of the description, so it is derived at
  load time rather than trusted from the index. An index written by an earlier version therefore gets the
  current extraction with no rebuild, and two installs score identical text for the same `SKILL.md`.
- **Failure vocabulary.** Symptom phrasing is the normal way to report a problem ("it crashes", "rows are
  incorrect", "we have a regression") while the skills that resolve it are written in causes ("debugging",
  "error recovery"). `SYNONYMS` gains entries bridging the two. They map onto problem/triage vocabulary
  only, never onto a domain noun: mapping "stuck" onto `queue` pulled message-board skills into a debugging
  query and cost a wrong-skill case, so only the triage half is kept.
- BROKEN retrieval 0.533 → 0.733, Hit@5 0.884 → 0.919, Hit@1 0.744 → 0.756, positive selection
  0.779 → 0.814. no-match accuracy (0.909), no-match safety (1.0), ambiguity accuracy (1.0) and
  wrong-skill rate (0.034) all unchanged. Median latency unchanged (54.0 → 53.6 ms interleaved on a
  loaded machine, within run-to-run noise).

- **Evidence by provenance.** Strong fields are now split by where their text comes from. `name`,
  `aliases` and `triggers` are declared by the skill about itself and count on any rare term; `keywords`
  is derived from prose (82% of its tokens come from the description) and counts only on very rare terms
  (≤3% of the corpus); `description` and `body` still never count. This stops ordinary prose from
  selecting a skill: "Refactor this module to remove the duplication." no longer answers with a skill
  that merely mentions `module`. Wrong-skill rate 0.043 → 0.034 on the 117-case suite, with no-match
  accuracy unchanged at 0.909.
- **Query/document symmetry.** A hyphenated query token is now split into its parts as well as kept
  whole, mirroring the name field, so `multi-tenant` can reach the name part `tenant`. Trivial
  inflections are folded on the query side exactly as the corpus-context check already folded them, so
  `vendors` reaches a skill named `vendor-management` and `SLOs` reaches `SLO`. Folded and split forms
  are only added, never substituted.
- Metrics on the 117-case suite: Hit@1 0.709 → 0.744, Hit@5 0.860 → 0.884, positive selection
  0.767 → 0.779, multi-skill chained 11 → 12, wrong-skill 0.043 → 0.034; no-match accuracy (0.909),
  no-match safety (1.0), ambiguity accuracy (1.0) and path accuracy (0.942) all held. BROKEN retrieval
  in the per-path report rose 0.400 → 0.533.
- Median latency 17.7 → 19.0 ms on 468 skills: folded and split tokens enlarge the query term set, so
  BM25's inner loop does more work. Still ~2.6x under the 50 ms target.

- **No-match detection (P0).** A lexical evidence gate now runs before any selection: noise/stopword
  and numeric queries, questions about the world with no work verb, and queries whose content terms
  mostly appear in no indexed skill are refused. A winner must show a skill-name claim or at least one
  rare, non-generic term in a strong field (`name`, `aliases`, `triggers`, `keywords`); description and
  body no longer count as evidence. Rareness is document frequency in the local corpus, so the gate
  scales with catalogue size. Curated-suite no-match accuracy 0.333 → 1.000 (12 cases) and 0.182 →
  0.909 (117 cases), with wrong-skill rate 0.111 → 0.043.
- **`usewhen` field.** Applicability sentences ("Use when …", "best for …", "triggers: …") are lifted
  out of each description and scored at weight 2.2. Derived at load time, so existing indexes keep
  working with no reindex and no schema change.
- **Operation words are not evidence.** Triage verbs (`refactor`, `review`, `deploy`, …) never identify
  a skill by themselves, unless the skill claims the term in its own name, aliases or triggers.
- **Path triage (P1).** BROKEN / BUILD / OPERATE / GENERAL classification with whole-word matching and
  position-aware precedence (a problem signal wins; otherwise the leading verb decides), path-aware
  thresholds that can only raise the configured bar, path-aware reason text, and `path` exposed in the
  decision JSON. Curated-suite path accuracy 0.733 → 0.942.
- **Latency.** Query preparation and per-field BM25 constants are computed once per route instead of
  once per scored record. Median routing latency on a 468-skill corpus: 55 ms → 18 ms.
- **Evaluation.** Suite expanded 12 → 117 curated cases with path labels; `evaluate.py` now reports
  per-path accuracy, no-match safety, wrong-skill rate and p95 latency.
- Added `tests/test_no_match.py`, `tests/test_use_when.py`, `tests/test_path_triage.py` (54 new tests).

## 0.1.0 - 2026-09-24

- Initial local-first Agent Skills index and lexical routing CLI.
- Added portable meta-skill, confidence decisions, project route overlays, optional semantic reranking, local learning, and adapter capability model.
- Added an evaluation fixture for a locally installed 459-skill corpus.
- Added conservative weak-intent handling, reduced generic-token false matches, and tightened multi-skill support selection.
- Added multi-case ambiguity/no-match evaluation and JSON corpus metadata; semantic commands now run without a shell.
- Added package/build artifact ignores and SPDX MIT package metadata.

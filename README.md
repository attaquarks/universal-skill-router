# Universal Skill Router

Universal Skill Router helps an agent find and load the right instructions from an existing Agent Skills library. It indexes directories containing `SKILL.md`, ranks relevant skills locally, explains its evidence, and produces an activation or child-agent handoff payload. It is a routing layer; it does not replace skill libraries, marketplaces, personas, or platform integrations.

The core runs offline with Python 3.10+ and the standard library. Optional semantic reranking is disabled by default. The portable meta-skill is in [`meta-skill/SKILL.md`](meta-skill/SKILL.md).

## Quick start

```powershell
python -m pip install .
skillrouter config add-root "$HOME/.agents/skills"
skillrouter index
skillrouter route "Design a RAG pipeline for our product docs"
skillrouter route "Review this pull request for security issues" --json
```

Or run from a checkout with `python -m skillrouter.cli`. Set `SKILLROUTER_HOME` to choose where configuration, index, events, and learning files live.

Install the portable skill into any skill root:

```powershell
skillrouter install-meta "$HOME/.agents/skills"
```

Then add the normal platform instruction that tells your agent to consult `universal-skill-router` on non-trivial tasks. Integration setup differs by platform; see [docs/integrations.md](docs/integrations.md).

## What happens

```text
SKILL.md roots → bounded local index → lexical retrieval and body evidence
                                      → confidence gate → decision / ambiguity
                                      → platform adapter loads selected instructions
                                      → optional local outcome signals
```

The index stores names, descriptions, metadata, headings, bounded text excerpts, hashes, and paths. It updates changed entries by content hash and skips unchanged files. Only candidate records are surfaced in route output. No scripts in a skill are executed.

## Commands

| Command | Purpose |
|---|---|
| `skillrouter index [--root DIR]` | Build or update the local catalog |
| `skillrouter find QUERY` | Fast local ranked retrieval |
| `skillrouter route QUERY [--semantic]` | Decide among `USE_SKILL`, `MULTI_SKILL`, `AMBIGUOUS`, `NO_MATCH` |
| `skillrouter explain QUERY` | JSON decision with candidate evidence and scores |
| `skillrouter list` | List indexed skills |
| `skillrouter doctor` | Check roots and index paths |
| `skillrouter stats` | Show index counts and configured roots |
| `skillrouter learn` | Aggregate local routing outcome signals |
| `skillrouter record EVENT SKILL` | Record loaded, ignored, override, or outcome event |
| `skillrouter handoff QUERY` | Emit a route payload for a child agent |
| `skillrouter config show` | Display settings |
| `skillrouter config add-root DIR` | Add a skill root |
| `skillrouter config set-enforcement MODE` | Set advisory, auto, or enforced intent for adapters |
| `skillrouter install-meta DIR` | Copy the portable router skill |

JSON is available with `--json`; global options work before or after the subcommand. `route --semantic` only uses a subprocess explicitly configured in local settings and passes JSON on stdin. Never configure a command that executes skill content.

## Configuration

Configuration is JSON at `~/.skillrouter/config.json` (or `$SKILLROUTER_HOME/config.json`). Example:

```json
{
  "roots": ["C:/Users/me/.agents/skills", "D:/shared/skills"],
  "confidence": {"use": 0.62, "support": 0.47, "margin": 0.05, "no_match": 0.28},
  "routing": {"max_candidates": 5, "max_chain_skills": 3, "prefer_orchestrators": true},
  "learning": {"enabled": true, "store_raw_prompts": false, "min_events": 3},
  "projects": [{
    "name": "Example",
    "aliases": ["example", "example-app"],
    "preferred_skills": ["frontend-specialist"],
    "routes": [{"when": ["release readiness"], "chain": ["release-check", "security-review"]}]
  }]
}
```

Project preferences and chains are local overlays; they do not change upstream skill files. Confidence values are calibrated heuristics, not statistical probabilities. Review `candidates`, `evidence`, and `reason` when tuning them.

## Integrations and activation

The core returns an activation list of allowed `SKILL.md` paths. A platform adapter decides how to load them. This CLI cannot inject instructions into a running, arbitrary agent session by itself. See [docs/integrations.md](docs/integrations.md) for capability boundaries and the portable skill path.

## Evaluation

The included suite is `eval/claude_skills_cases.json`. Run `skillrouter index --root <skills-dir>`, then `python scripts/evaluate.py --output outputs/evaluation.json`. Its labels are manually curated, broad relevance sets rather than blinded human judgments, so the reported retrieval figures are a smoke benchmark, not a general accuracy claim. The checked-in report currently records Hit@1/3/5 of 1.0, ambiguity accuracy 1.0, and no-match accuracy 0.333 over 12 cases; the weak no-match result exposes a known limitation of lexical-only out-of-domain detection. Median route time was about 49 ms. `outputs/claude-skills-evaluation.json` captures a run against the 459 skills installed in the development environment, not a freshly cloned upstream repository; the installed library's upstream provenance was not independently verified.

## Safety and privacy

The scanner is limited to configured roots, does not follow directory symlinks by default, resolves candidate paths against the scanned root, imposes a per-file size cap, and reads no scripts. Learning stores token signals and hashes instead of raw prompts by default. Everything stays local unless a user configures an external semantic command.

See [SECURITY.md](SECURITY.md), [docs/architecture.md](docs/architecture.md), [docs/setup.md](docs/setup.md), and [CONTRIBUTING.md](CONTRIBUTING.md).

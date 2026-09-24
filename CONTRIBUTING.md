# Contributing

Contributions should keep the core platform-neutral, local-first, and compatible with standard Agent Skills directories. Do not add a hardcoded catalog of skill names or edit third-party skills.

## Development

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
skillrouter --help
```

Keep tests offline and self-contained. Add representative queries to `eval/claude_skills_cases.json` only with clear expected labels; report the corpus used and avoid general accuracy claims from a small fixture.

## Changes

- Explain user-visible behavior and configuration changes.
- Keep index schemas versioned and migration behavior safe.
- Bound reads and subprocess timeouts.
- Never run scripts discovered in skill directories.
- Document adapter capabilities instead of assuming host hooks.
- Include a changelog entry for user-visible changes.

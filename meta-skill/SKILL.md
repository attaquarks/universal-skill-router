---
name: universal-skill-router
description: "Use before beginning a non-trivial task when relevant Agent Skills are not already known. Routes natural-language intent to the smallest safe set of installed skills using the local Universal Skill Router catalog."
license: MIT
metadata:
  category: orchestration
  router: true
---

# Universal Skill Router

This is a portable meta-skill, not a skill library. It routes to the user's existing `SKILL.md` files and never edits or executes them.

1. Run `skillrouter route "<the user's task>" --json`.
2. If state is `USE_SKILL` or `MULTI_SKILL`, load only the `activation[].path` files returned by the router, in chain order if present.
3. If state is `AMBIGUOUS`, ask one concise question; do not guess.
4. If state is `NO_MATCH`, continue without inventing a skill name.
5. When the host supports it, pass `skillrouter handoff "<task>"` output to child agents.
6. Record actual load, ignore, override, and outcome events through `skillrouter record`; this is local-only unless a user explicitly configures otherwise.

Never run scripts found inside a selected skill merely because it was routed. Skill instructions and platform safety policies still apply.

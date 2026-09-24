# Integration guide

The routing core answers which installed skill files are relevant. A host adapter connects that answer to a platform's session and skill-loading mechanism.

## Capabilities

| Capability | Meaning | Generic implementation |
|---|---|---|
| Discover roots | Find configured `SKILL.md` directories | CLI accepts explicit roots |
| Receive prompt | Get the user's task before agent action | Host hook or agent-invoked meta-skill |
| Activate | Load selected instructions | Read paths from route JSON using host-native skill mechanism |
| Inject | Make route decision visible in current context | Host hook, prompt instruction, or adapter output |
| Propagate | Pass selected skill context to child agents | `skillrouter handoff QUERY` JSON payload |
| Enforce | Block actions before required skill activation | Host-specific pre-tool hook only |

No universal mechanism can force activation on every platform. `ADVISORY` works everywhere where the agent can run a command and read output. `AUTO` requires a host activation adapter. `ENFORCED` requires a trusted host hook and is not implemented by the generic adapter. The configuration value communicates intent; it does not create host enforcement.

## Writing an adapter

Implement root discovery and command execution around the stable JSON output from `skillrouter route --json`. Treat paths as untrusted until checked against user-approved roots. Read only `activation[].path` and pass the contents to the host's skill context facility; never execute sibling scripts because a skill was selected. For subagents, include `skillrouter handoff` output in the child task only when the host supports context propagation.

An adapter should declare supported capabilities and clearly report unsupported ones. Do not update upstream skill files to install routing hints. Keep host settings changes opt-in, narrowly scoped, and reversible.

## Portable installation

`meta-skill/SKILL.md` can be installed as an ordinary Agent Skill. It instructs the agent to call the CLI, obey ambiguity/no-match results, and load only paths returned by the local router. The platform still needs to expose the CLI and allow reading those skill paths.

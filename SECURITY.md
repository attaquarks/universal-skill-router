# Security policy

## Reporting

Please report vulnerabilities privately to the repository maintainer. Include the affected version, reproduction steps, and impact. Do not include real user prompts, private skills, or secrets in public issues.

## Trust boundaries

Installed skills are untrusted text. Indexing never imports or executes skill code. The scanner reads only `SKILL.md`, enforces configured-root containment, skips directory symlinks unless explicitly enabled, and rejects oversized files. Activation returns instruction paths; a host adapter must not treat selection as permission to execute scripts.

The optional semantic command is administrator-configured executable code. It receives the query and selected candidate excerpts on stdin. It is disabled by default. Configure only a trusted local command and review what data it receives.

Learning data is local by default. Raw prompt storage is disabled by default; token signals and a prompt hash may still reveal limited information, so protect local state files as you would other agent metadata.

# Setup

## Install from source

Requires Python 3.10 or newer. The router uses only the Python standard library.

```sh
python -m pip install .
skillrouter config add-root ~/.agents/skills
skillrouter index
skillrouter doctor
```

On Windows PowerShell, use `$HOME\.agents\skills`. Add other skill roots with another `config add-root` invocation or list them in `config.json`.

## Portable meta-skill

Copy it into an Agent Skills directory using:

```sh
skillrouter install-meta ~/.agents/skills
```

Restart or refresh the host if it caches skills. See [integrations.md](integrations.md) before expecting prompt interception: the meta-skill requires the host to make the CLI available and to load returned paths.

## Local state

By default, files live in `~/.skillrouter/`:

- `config.json`: roots, thresholds, project overlays, adapter preferences
- `index.json`: generated catalog
- `events.jsonl`: local outcome signals
- `learned.json`: derived token/skill boosts and transition counts

Set `SKILLROUTER_HOME` to move this state. Back up or remove these files using your usual local data controls.

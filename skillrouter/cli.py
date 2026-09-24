from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from .adapters import adapter_manifest
from .config import APP_DIR, CONFIG_PATH, INDEX_PATH, default_roots, load_config, save_config
from .engine import Router
from .indexer import Indexer, load_records
from .learning import learn, record_event
from .models import DecisionState, under_root


def _emit(value: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True))
        return
    if isinstance(value, dict) and "state" in value:
        print(f"[{value['state']}] confidence={value.get('confidence', 0):.2f} latency={value.get('latency_ms', 0):.2f}ms")
        if value.get("primary"): print(f"Primary: {value['primary']['name']}")
        if value.get("supporting"): print("Supporting: " + ", ".join(item["name"] for item in value["supporting"]))
        print("Reason: " + value.get("reason", ""))
        if value.get("state") == "AMBIGUOUS": print("Question: Which outcome or platform is most important for this task?")
        return
    print(json.dumps(value, indent=2, sort_keys=True) if isinstance(value, (dict, list)) else value)


def _config(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config(Path(args.config) if args.config else CONFIG_PATH)
    if getattr(args, "root", None): config["roots"] = args.root
    return config


def _router(args: argparse.Namespace) -> tuple[dict[str, Any], Router]:
    config = _config(args)
    return config, Router(config)


def cmd_index(args: argparse.Namespace) -> int:
    config = _config(args)
    if not config["roots"]: config["roots"] = default_roots()
    if not config["roots"]:
        print("No skill roots configured. Use skillrouter config add-root <directory> or skillrouter index --root <directory>.", file=sys.stderr)
        return 2
    payload = Indexer(config["roots"], config).build()
    _emit(payload["stats"] | {"roots": payload["roots"]}, args.json)
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    config, router = _router(args)
    if not router.records:
        print("No index found. Run skillrouter index first.", file=sys.stderr)
        return 2
    decision = router.route(args.query, semantic=getattr(args, "semantic", False))
    payload = decision.to_dict()
    if config.get("learning", {}).get("enabled", True) and decision.primary:
        record_event({"event": "recommended", "prompt": args.query, "skill": decision.primary.name, "state": decision.state.value, "confidence": decision.confidence}, config)
    _emit(payload, args.json)
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    args.semantic = False
    return cmd_route(args)


def cmd_explain(args: argparse.Namespace) -> int:
    args.semantic = getattr(args, "semantic", False)
    args.json = True
    return cmd_route(args)


def cmd_list(args: argparse.Namespace) -> int:
    records = load_records()
    value = [{"name": item.name, "domain": item.domain, "path": item.path, "orchestrator": item.is_orchestrator, "warnings": item.warnings} for item in records]
    _emit(value, args.json)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    config = _config(args)
    records = load_records()
    values = {"app_dir": str(APP_DIR), "indexed_skills": len(records), "roots": config["roots"],
        "orchestrators_detected": sum(record.is_orchestrator for record in records), "index_path": str(INDEX_PATH)}
    _emit(values, args.json)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = _config(args)
    records = load_records()
    if not config["roots"] and INDEX_PATH.exists():
        try: config["roots"] = json.loads(INDEX_PATH.read_text(encoding="utf-8")).get("roots", [])
        except (OSError, json.JSONDecodeError): pass
    problems = []
    if not records: problems.append("index is empty; run `skillrouter index`")
    for root in config["roots"]:
        if not Path(root).expanduser().exists(): problems.append(f"unavailable root: {root}")
    for record in records:
        path = Path(record.path)
        if not path.exists(): problems.append(f"stale skill path: {record.name}")
        elif not any(under_root(path, Path(root)) for root in config["roots"] if Path(root).exists()): problems.append(f"unsafe indexed path: {record.name}")
    value = {"ok": not problems, "problems": problems, "checks": {"index": bool(records), "roots": len(config["roots"]), "adapters": adapter_manifest()}}
    _emit(value, args.json)
    return 0 if not problems else 1


def cmd_learn(args: argparse.Namespace) -> int:
    value = learn(_config(args))
    _emit({"events": value.get("event_count", 0), "learned_tokens": len(value.get("token_skill_boosts", {})), "outcomes": len(value.get("skill_outcomes", {}))}, args.json)
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    config = _config(args)
    record_event({"event": args.event, "skill": args.skill, "prompt": args.query or "", "outcome": args.outcome or ""}, config)
    _emit({"recorded": bool(config.get("learning", {}).get("enabled", True))}, args.json)
    return 0


def cmd_handoff(args: argparse.Namespace) -> int:
    _, router = _router(args)
    _emit(router.handoff(router.route(args.query, semantic=args.semantic)), True)
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    config_path = Path(args.config) if args.config else CONFIG_PATH
    config = load_config(config_path)
    if args.config_action == "show": _emit(config, args.json); return 0
    if args.config_action == "add-root":
        root = str(Path(args.value).expanduser().resolve())
        if root not in config["roots"]: config["roots"].append(root)
    elif args.config_action == "set-enforcement":
        config["enforcement"]["mode"] = args.value.upper()
    elif args.config_action == "set-learning":
        config["learning"]["enabled"] = args.value.lower() in ("1", "true", "on", "yes")
    save_config(config, config_path)
    _emit({"saved": str(config_path)}, args.json)
    return 0


def cmd_install_meta(args: argparse.Namespace) -> int:
    source = Path(__file__).resolve().parent.parent / "meta-skill"
    target = Path(args.destination).expanduser().resolve() / "universal-skill-router"
    if target.exists() and not args.force:
        print(f"Refusing to overwrite {target}; pass --force after review.", file=sys.stderr); return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists(): shutil.rmtree(target)
    shutil.copytree(source, target)
    _emit({"installed": str(target / "SKILL.md")}, args.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skillrouter", description="Local-first routing for existing Agent Skills")
    parser.add_argument("--config", help="Path to router config.json")
    parser.add_argument("--root", action="append", help="Skill root (repeatable; overrides configured roots for this command)")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index"); index.set_defaults(func=cmd_index)
    for name, func in (("find", cmd_find), ("route", cmd_route), ("explain", cmd_explain)):
        command = sub.add_parser(name); command.add_argument("query"); command.add_argument("--semantic", action="store_true"); command.set_defaults(func=func)
    sub.add_parser("list").set_defaults(func=cmd_list)
    sub.add_parser("stats").set_defaults(func=cmd_stats)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    sub.add_parser("learn").set_defaults(func=cmd_learn)
    record = sub.add_parser("record"); record.add_argument("event", choices=["loaded", "ignored", "override", "outcome"]); record.add_argument("skill"); record.add_argument("--query"); record.add_argument("--outcome"); record.set_defaults(func=cmd_record)
    handoff = sub.add_parser("handoff"); handoff.add_argument("query"); handoff.add_argument("--semantic", action="store_true"); handoff.set_defaults(func=cmd_handoff)
    config = sub.add_parser("config"); config_sub = config.add_subparsers(dest="config_action", required=True)
    config_sub.add_parser("show")
    for action in ("add-root", "set-enforcement", "set-learning"):
        item = config_sub.add_parser(action); item.add_argument("value")
    config.set_defaults(func=cmd_config)
    install = sub.add_parser("install-meta"); install.add_argument("destination"); install.add_argument("--force", action="store_true"); install.set_defaults(func=cmd_install_meta)
    adapters = sub.add_parser("adapters"); adapters.set_defaults(func=lambda args: (_emit(adapter_manifest(), args.json) or 0))
    return parser


def main(argv: list[str] | None = None) -> int:
    # Accept global options both before and after a subcommand, matching normal CLI expectations.
    raw = list(sys.argv[1:] if argv is None else argv)
    global_args: list[str] = []
    remaining: list[str] = []
    index = 0
    while index < len(raw):
        item = raw[index]
        if item == "--json":
            global_args.append(item)
        elif item in ("--config", "--root"):
            if index + 1 >= len(raw):
                raise SystemExit(f"{item} requires a value")
            global_args.extend([item, raw[index + 1]])
            index += 1
        else:
            remaining.append(item)
        index += 1
    args = build_parser().parse_args(global_args + remaining)
    return int(args.func(args))


if __name__ == "__main__": raise SystemExit(main())

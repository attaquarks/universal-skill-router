from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    discover_roots: bool = True
    inject_instructions: bool = False
    activate_skills: bool = False
    receive_prompt: bool = False
    propagate_route: bool = False
    enforce: bool = False


class Adapter:
    """Platform boundary. The core never imports a platform SDK or assumes hooks exist."""
    name = "generic"
    capabilities = AdapterCapabilities()

    def activation_payload(self, route: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "manual", "instructions": "Read only the approved SKILL.md paths in route.activation.", "route": route}


class FilePathAdapter(Adapter):
    name = "file-path"
    capabilities = AdapterCapabilities(activate_skills=True, propagate_route=True)

    def activation_payload(self, route: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "file-path", "load": route.get("activation", []), "handoff": route}


ADAPTERS = {"generic": Adapter(), "file-path": FilePathAdapter()}


def adapter_manifest() -> dict[str, Any]:
    return {name: {field: getattr(adapter.capabilities, field) for field in adapter.capabilities.__dataclass_fields__} for name, adapter in ADAPTERS.items()}

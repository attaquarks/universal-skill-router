"""Universal Skill Router: routing, not a replacement skill ecosystem."""

from .engine import Router
from .models import DecisionState

__all__ = ["Router", "DecisionState"]
__version__ = "0.1.0"

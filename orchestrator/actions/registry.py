"""Action registry — same pattern as services/tools.py.

register_action(spec): adds ActionSpec to the in-memory registry and fires
  Event.ACTION_REGISTERED so SSE and plugins know a new action is available.
get_action(name): returns ActionSpec or None.
list_actions(): returns all registered specs (metadata only, no handler callable).
"""

import logging

import hooks
from events import Event
from models.actions import ActionSpec

_log = logging.getLogger(__name__)

_registry: dict[str, ActionSpec] = {}


def register_action(spec: ActionSpec) -> None:
    """Register an ActionSpec. Fires ACTION_REGISTERED hook."""
    _registry[spec.name] = spec
    hooks.fire(
        Event.ACTION_REGISTERED,
        action_name=spec.name,
        connector=spec.connector,
        action_type=spec.action_type,
        is_write=spec.is_write,
    )
    _log.info("Action registered: %s (connector=%s, write=%s)", spec.name, spec.connector, spec.is_write)


def get_action(name: str) -> ActionSpec | None:
    return _registry.get(name)


def list_actions() -> list[dict]:
    """Return action metadata without the handler callable."""
    return [
        {
            "name": s.name,
            "connector": s.connector,
            "action_type": s.action_type,
            "is_write": s.is_write,
            "is_reversible": s.is_reversible,
            "reverse_action_name": s.reverse_action_name,
            "definition": s.definition,
        }
        for s in _registry.values()
    ]

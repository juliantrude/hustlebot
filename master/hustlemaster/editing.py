"""Plan editing from the graph view (ADR 0013): the user changes an increment's prompt or model, deletes it or
pauses it. Dashboard only — no agent reaches this, so a change here is the user's own and confirms the plan
(`Store.change_plan(confirm=True)`), unless an overseer's change still waits for their decision.

One edit is the JSON body graph-engine sends to the graph's `edit_url`:
`{"node": id, "field": "prompt" | "model", "value": text}` or `{"node": id, "action": "pause" | "resume" | "delete"}`.
"""

from __future__ import annotations

from dataclasses import replace

from .model import POINTS, Hustle, Increment
from .store import Store

FIELDS = ("prompt", "model")
ACTIONS = ("pause", "resume", "delete")


def apply(store: Store, hustle_id: str, edit: dict) -> None:
    """Apply one edit; ValueError or KeyError says why it was refused, and nothing changed then."""
    hustle = store.hustle(hustle_id)
    if hustle is None:
        raise KeyError(f"unknown hustle {hustle_id!r}")
    target = next((i for i in hustle.increments if i.id == edit.get("node")), None)
    if target is None:
        raise KeyError(f"unknown increment {edit.get('node')!r}")
    action = edit.get("action")
    if action is not None:
        if action not in ACTIONS:
            raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
        if action == "delete":
            return _delete(store, hustle, target)
        return store.set_paused(hustle_id, target.id, action == "pause")
    field, value = edit.get("field"), edit.get("value")
    if field not in FIELDS:
        raise ValueError(f"field must be one of {', '.join(FIELDS)}")
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if target.state == "done":
        raise ValueError(f"{target.id} is done; a change would never run")
    if field == "prompt":
        if not value.strip():
            raise ValueError("a prompt may not be empty")
        changed = replace(target, prompt=value)
    else:
        if value not in POINTS:
            raise ValueError(f"unknown model {value!r}")
        changed = replace(target, model=value)
    if changed == target:
        return
    increments = [changed if i.id == target.id else i for i in hustle.increments]
    store.change_plan(hustle_id, increments, author="user", note=f"{field} of {target.id} edited", confirm=True)
    if target.state == "blocked":
        store.reopen(hustle_id, target.id)


def _delete(store: Store, hustle: Hustle, target: Increment) -> None:
    if target.state == "running":
        raise ValueError(f"{target.id} is running; pause it and delete it once its session ended")
    increments = []
    for i in hustle.increments:
        if i.id == target.id:
            continue
        # its dependants keep the order it stood for: they now wait for what it waited for
        after = [d for dep in i.after for d in ([*target.after] if dep == target.id else [dep])]
        increments.append(replace(i, after=list(dict.fromkeys(after))))
    store.change_plan(hustle.id, increments, author="user", note=f"{target.id} deleted", confirm=True)

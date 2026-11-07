"""A hustle as a graph-engine/1 document (../graph-engine, docs/format.md): one node type per model,
one state per increment state, the prompt in the click panel."""

from __future__ import annotations

from .live import project_name
from .model import POINTS, Hustle, Increment

# Colour slots of the engine, chosen to stay clear of the state colours (green, blue, red, grey).
MODEL_TYPES = [
    {"id": "haiku", "label": "Haiku · 1 point", "slot": 5, "glyph": "H"},
    {"id": "sonnet", "label": "Sonnet · 2 points", "slot": 1, "glyph": "S"},
    {"id": "opus", "label": "Opus · 4 points", "slot": 4, "glyph": "O"},
    {"id": "fable", "label": "Fable · 8 points", "slot": 7, "glyph": "F"},
]

# why an open increment does not start, in the tag line
HELD = {"paused": "paused", "overseer": "with an overseer", "approval": "awaits approval",
        "provisional": "provisional, awaits approval", "fable": "Fable is switched off",
        "points": "costs more than the total points"}

STATES = [
    {"id": "done", "look": "strong", "label": "Done"},
    {"id": "running", "look": "active", "label": "Running"},
    {"id": "open", "look": "muted", "label": "Open"},
    {"id": "blocked", "look": "critical", "label": "Blocked", "badge": "⛔"},
]


def graph_doc(hustle: Hustle, *, allow_fable: bool = True) -> dict:
    """The graph view's document. It carries the dashboard's edit route (`editing.py`): what an increment's state
    allows to change is offered in its panel."""
    done = sum(i.state == "done" for i in hustle.increments)
    return {
        "schema": "graph-engine/1",
        "title": hustle.title,
        "subtitle": f"{project_name(hustle.project)} · {done}/{len(hustle.increments)} done",
        "storage_key": "hustlebot-theme",
        "edit_url": f"/api/hustles/{hustle.id}/edit",
        "words": {
            "nodes": "increments",
            "panel_hint": "Click an increment to see its prompt, change it, pause or delete it.",
        },
        "types": MODEL_TYPES,
        "states": STATES,
        "nodes": [_node(i, allow_fable) for i in hustle.increments],
        "edges": [{"from": dep, "to": i.id} for i in hustle.increments for dep in i.after],
    }


def _node(inc: Increment, allow_fable: bool) -> dict:
    prompt = {"label": "Prompt", "lines": inc.prompt.splitlines() or inc.instructions}
    model = {"label": "Model", "lines": [f"{inc.model} · {POINTS[inc.model]} points"]}
    paused = inc.held == "paused"
    # the canvas draws the title only: a pause has to show there, the panel names every hold
    node = {"id": inc.id, "type": inc.model, "title": f"{'⏸ ' if paused else ''}{inc.id} · {inc.title}",
            "state": inc.state, "details": [prompt, model]}
    if inc.held:
        node["tags"] = [HELD.get(inc.held, inc.held)]
        node["details"].append({"label": "Waits", "lines": [HELD.get(inc.held, inc.held)]})
    if inc.state == "done":
        return node  # a change would never run
    # a running increment's change applies to its next attempt
    prompt["edit"] = {"field": "prompt", "value": inc.prompt}
    choices = [m for m in POINTS if allow_fable or m != "fable" or m == inc.model]
    model["edit"] = {"field": "model", "value": inc.model, "choices": choices}
    node["actions"] = [{"id": "resume", "label": "Resume"} if paused else {"id": "pause", "label": "Pause"}]
    if inc.state != "running":
        node["actions"].append({"id": "delete", "label": "Delete", "confirm": f"Delete {inc.id}? Click again"})
    return node

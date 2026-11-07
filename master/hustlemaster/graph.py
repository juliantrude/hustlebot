"""A hustle as a graph-engine/1 document (../graph-engine, docs/format.md): one node type per model,
one state per increment state, the instructions in the click panel."""

from __future__ import annotations

from .model import POINTS, Hustle

# Colour slots of the engine, chosen to stay clear of the state colours (green, blue, red, grey).
MODEL_TYPES = [
    {"id": "haiku", "label": "Haiku · 1 point", "slot": 5, "glyph": "H"},
    {"id": "sonnet", "label": "Sonnet · 2 points", "slot": 1, "glyph": "S"},
    {"id": "opus", "label": "Opus · 4 points", "slot": 4, "glyph": "O"},
    {"id": "fable", "label": "Fable · 8 points", "slot": 7, "glyph": "F"},
]

STATES = [
    {"id": "done", "look": "strong", "label": "Done"},
    {"id": "running", "look": "active", "label": "Running"},
    {"id": "open", "look": "muted", "label": "Open"},
    {"id": "blocked", "look": "critical", "label": "Blocked", "badge": "⛔"},
]


def graph_doc(hustle: Hustle) -> dict:
    done = sum(i.state == "done" for i in hustle.increments)
    return {
        "schema": "graph-engine/1",
        "title": hustle.title,
        "subtitle": f"{hustle.project} · {done}/{len(hustle.increments)} done",
        "storage_key": "hustlebot-theme",
        "words": {
            "nodes": "increments",
            "panel_hint": "Click an increment to see its instructions.",
        },
        "types": MODEL_TYPES,
        "states": STATES,
        "nodes": [
            {
                "id": i.id,
                "type": i.model,
                "title": f"{i.id} · {i.title}",
                "state": i.state,
                "details": [
                    {"label": "Instructions", "lines": i.instructions},
                    {"label": "Model", "lines": [f"{i.model} · {POINTS[i.model]} points"]},
                ],
            }
            for i in hustle.increments
        ],
        "edges": [{"from": dep, "to": i.id} for i in hustle.increments for dep in i.after],
    }

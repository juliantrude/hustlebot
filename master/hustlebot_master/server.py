"""The master's web app on 127.0.0.1: the dashboard, its JSON API and the graph-engine page.

    python3 -m hustlebot_master.server --mock     # from master/; made-up chains (mock.py)
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import mock
from .graph import graph_doc
from .model import POINTS, shares

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard"
STATIC = {"/": "index.html", "/app.js": "app.js", "/app.css": "app.css"}
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}


def engine_page() -> Path:
    """The bundled graph-engine page: from the installed package, else from a sibling checkout."""
    try:
        from graph_engine import page_path

        return page_path()
    except ImportError:
        return Path(__file__).resolve().parents[3] / "graph-engine/src/graph_engine/page/index.html"


class State:
    def __init__(self) -> None:
        self.total_points = 20
        self.chains = mock.chains()
        self.approvals = list(mock.APPROVALS)

    def chain(self, chain_id: str):
        return next((c for c in self.chains if c.id == chain_id), None)

    def summary(self) -> dict:
        share = shares(self.chains, self.total_points)
        order = sorted(self.chains, key=lambda c: (not c.active, -c.weight, c.title))
        return {
            "total_points": self.total_points,
            "points_in_use": sum(c.points_in_use() for c in self.chains),
            "model_points": POINTS,
            "usage": mock.USAGE,
            "approvals": self.approvals,
            "chains": [
                {
                    "id": c.id,
                    "project": c.project,
                    "title": c.title,
                    "active": c.active,
                    "weight": c.weight,
                    "status": c.status(),
                    "done": sum(i.state == "done" for i in c.increments),
                    "total": len(c.increments),
                    "share": round(share.get(c.id, 0), 1),
                    "points_in_use": c.points_in_use(),
                    "sessions": [{"increment": i.id, "model": i.model} for i in c.running()],
                }
                for c in order
            ],
        }


def make_handler(state: State, engine: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, value, status: int = 200) -> None:
            self._send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json")

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path in STATIC:
                file = DASHBOARD / STATIC[path]
                return self._send(200, file.read_bytes(), TYPES[file.suffix])
            if path in ("/engine/", "/engine/index.html"):
                return self._send(200, engine.read_bytes(), "text/html")
            if path == "/api/state":
                return self._json(state.summary())
            match = re.fullmatch(r"/api/chains/([\w-]+)/graph\.json", path)
            if match and (chain := state.chain(match.group(1))):
                return self._json(graph_doc(chain))
            self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/settings":
                state.total_points = max(1, int(body.get("total_points", state.total_points)))
                return self._json(state.summary())
            match = re.fullmatch(r"/api/chains/([\w-]+)", self.path)
            if match and (chain := state.chain(match.group(1))):
                if "active" in body:
                    chain.active = bool(body["active"])
                if "weight" in body:
                    chain.weight = max(1, int(body["weight"]))
                return self._json(state.summary())
            match = re.fullmatch(r"/api/approvals/([\w-]+)", self.path)
            if match:
                state.approvals = [a for a in state.approvals if a["id"] != match.group(1)]
                return self._json(state.summary())
            self._json({"error": "not found"}, 404)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mock", action="store_true", help="serve made-up chains (the only mode so far)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not args.mock:
        parser.error("only --mock exists yet")
    engine = engine_page()
    if not engine.is_file():
        parser.error(f"graph-engine page not found at {engine}")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(State(), engine))
    print(f"hustlebot master (mock) on http://127.0.0.1:{args.port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()

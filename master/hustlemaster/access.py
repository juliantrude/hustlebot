"""Who may do what to the hustlemaster's plans: the rights table of ADR 0014, independent of the transport.

Every caller is a role, and a worker or overseer is bound to the one hustle it was started for. Dashboard-only
actions (approvals, activation, weights, settings) have no entry here on purpose: no agent can reach them, not
even from the user's own session.
"""

from __future__ import annotations

import os
import secrets
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from . import projects
from .model import Increment
from .store import Store

ROLES = ("worker", "overseer", "interactive")

RIGHTS: dict[str, frozenset[str]] = {
    "worker": frozenset({"read_hustle", "propose_change"}),
    "overseer": frozenset({"read_hustle", "change_plan", "read_settings"}),
    "interactive": frozenset({"list_hustles", "read_hustle", "submit_hustle", "change_plan", "read_settings"}),
}
ACTIONS = frozenset().union(*RIGHTS.values())


class Refused(PermissionError):
    pass


@dataclass(frozen=True)
class Caller:
    role: str
    hustle: str | None = None  # the hustle a worker or overseer belongs to; None for interactive sessions
    increment: str | None = None  # the increment the session runs, so proposals can say where they came from

    @property
    def author(self) -> str:
        return f"{self.role}:{self.increment}" if self.increment else self.role


class Tokens:
    """Bearer tokens → callers. Session tokens live in memory: a restarted hustlemaster restarts its sessions
    with new `--mcp-config` files anyway. The interactive token is fixed so the plugin's MCP entry can carry it."""

    def __init__(self, interactive: str | None = None) -> None:
        self._callers: dict[str, Caller] = {}
        self._lock = threading.Lock()
        if interactive:
            self._callers[interactive] = Caller("interactive")

    def issue(self, role: str, hustle: str, increment: str | None = None) -> str:
        if role not in ("worker", "overseer"):
            raise ValueError(f"session tokens are for workers and overseers, not {role!r}")
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._callers[token] = Caller(role, hustle, increment)
        return token

    def revoke(self, token: str) -> None:
        with self._lock:
            if self._callers.get(token, Caller("worker")).role != "interactive":
                self._callers.pop(token, None)

    def caller(self, token: str | None) -> Caller | None:
        if not token:
            return None
        with self._lock:
            return self._callers.get(token)


def token_file(path: Path) -> str:
    """Read a token (the interactive MCP entry's, the dashboard login's), creating it readable by the user only
    on first use."""
    if path.is_file():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    return token


class Gate:
    """The store as one caller may see it. Every method checks the right first, then the hustle binding."""

    def __init__(self, store: Store, caller: Caller, root: Path | None = None) -> None:
        """`root` holds the projects' repositories (ADR 0016); without one a project is an absolute path that
        must already be a repository here, as before."""
        self.store = store
        self.caller = caller
        self.root = root

    def allowed(self, action: str) -> bool:
        return action in RIGHTS.get(self.caller.role, frozenset())

    def list_hustles(self) -> list[dict]:
        self._right("list_hustles")
        return [
            {"id": h.id, "project": h.project, "title": h.title, "active": h.active, "status": h.status(),
             "version": h.version}
            for h in self.store.hustles()
        ]

    def read_hustle(self, hustle_id: str | None = None) -> dict:
        self._right("read_hustle")
        hustle_id = self._bound(hustle_id)
        h = self.store.hustle(hustle_id)
        if h is None:
            raise KeyError(f"unknown hustle {hustle_id!r}")
        return {
            "id": h.id,
            "project": h.project,
            "title": h.title,
            "brief": h.brief,
            "active": h.active,
            "status": h.status(),
            "version": h.version,
            "increments": [
                {k: v for k, v in asdict(i).items() if k != "instructions"} for i in h.increments
            ],
        }

    def propose_change(self, text: str, hustle_id: str | None = None) -> int:
        self._right("propose_change")
        return self.store.propose(self._bound(hustle_id), text, author=self.caller.author,
                                  increment=self.caller.increment)

    def change_plan(self, increments: list[Increment], note: str = "", hustle_id: str | None = None) -> dict:
        """The new version, and the approval it waits for if it went beyond the bounds of ADR 0015: the overseer
        must know that what it added will not run until the user says yes."""
        self._right("change_plan")
        hustle_id = self._bound(hustle_id)
        answer: dict = {"version": self.store.change_plan(hustle_id, increments, author=self.caller.author, note=note)}
        for pending in self.store.approvals(hustle_id, states=("pending",)):
            answer["approval"] = {"reasons": pending.reasons, "waiting": pending.waiting}
        return answer

    def submit_hustle(self, project: str, title: str, brief: str, increments: list[Increment], *,
                      commit: str | None = None, remote: str | None = None, push: str | None = None,
                      verify: str | None = None, required: list[str] | None = None) -> dict:
        """Prepare the project's repository here, check that the state the plan was made against has arrived, and
        store the hustle. The answer says what the user still has to do before it may start."""
        self._right("submit_hustle")
        if push is not None and push not in projects.PUSH_MODES:
            raise ValueError(f"push must be one of {', '.join(projects.PUSH_MODES)}")
        if self.root is None:
            if not Path(project).is_absolute():
                raise ValueError("project must be an absolute path to the repository")
            path = Path(project)
        else:
            path = projects.path_for(self.root, project)
            stored = self.store.project(str(path))
            projects.prepare(path, remote if remote is not None else (stored.remote if stored else None) or None)
            synced = projects.sync(path)
            if commit and not projects.contains(path, commit):
                hint = (f"push it to the project's remote ({remote or stored.remote})" if (remote or (stored and stored.remote))
                        else f"push it here: git push <this host>:{path} main")
                raise ValueError(f"commit {commit} is not in `main` of {path} yet; {hint}"
                                 + (f" ({synced})" if synced else ""))
        hustle_id = self.store.submit(str(path), title, brief, increments, author=self.caller.author, push=push,
                                      verify=verify, remote=remote, required=required)
        project_now = self.store.project(str(path))
        return {"hustle": hustle_id, "repository": str(path), "remote": project_now.remote,
                "push": project_now.push, "missing_files": projects.missing(path, project_now.required)}

    def read_settings(self) -> dict:
        self._right("read_settings")
        return asdict(self.store.settings())

    def _right(self, action: str) -> None:
        if not self.allowed(action):
            raise Refused(f"{self.caller.role} sessions may not {action.replace('_', ' ')}")

    def _bound(self, hustle_id: str | None) -> str:
        if self.caller.hustle is None:
            if not hustle_id:
                raise ValueError("hustle_id is required")
            return hustle_id
        if hustle_id and hustle_id != self.caller.hustle:
            raise Refused(f"{self.caller.role} sessions may only touch their own hustle")
        return self.caller.hustle

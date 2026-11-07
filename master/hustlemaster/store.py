"""The hustlemaster's database: hustles, versioned plans, increment progress and settings (ADR 0014).

A plan version is a full snapshot of the increments' definitions, so any version can be shown or exported as
it was. Progress (state, attempts, peak context) belongs to the increment id, not to a version: an edit
applies to the next attempt and must not reset what already happened.
"""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from . import approvals as judging, plan
from .model import STATES, Hustle, Increment

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects (
    path TEXT PRIMARY KEY,
    push INTEGER NOT NULL DEFAULT 0,
    copy_excludes TEXT,  -- JSON list overriding the default excludes for gitignored files (ADR 0011); NULL = default
    verify TEXT  -- shell command that checks the project after an increment's rebase onto main; NULL = none
);
CREATE TABLE IF NOT EXISTS hustles (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL REFERENCES projects(path),
    title TEXT NOT NULL,
    brief TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0,
    weight INTEGER NOT NULL DEFAULT 1 CHECK (weight >= 1),
    confirmed_version INTEGER,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_versions (
    hustle TEXT NOT NULL REFERENCES hustles(id),
    version INTEGER NOT NULL,
    author TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    PRIMARY KEY (hustle, version)
);
CREATE TABLE IF NOT EXISTS plan_increments (
    hustle TEXT NOT NULL,
    version INTEGER NOT NULL,
    position INTEGER NOT NULL,
    id TEXT NOT NULL,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    after TEXT NOT NULL,  -- JSON list of increment ids
    model TEXT NOT NULL,
    mode TEXT NOT NULL,
    PRIMARY KEY (hustle, version, id),
    FOREIGN KEY (hustle, version) REFERENCES plan_versions(hustle, version)
);
CREATE TABLE IF NOT EXISTS progress (
    hustle TEXT NOT NULL REFERENCES hustles(id),
    increment TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    attempts INTEGER NOT NULL DEFAULT 0,
    peak_context INTEGER NOT NULL DEFAULT 0,
    note TEXT,  -- what the increment's next session must know, e.g. why the last attempt failed; read once
    PRIMARY KEY (hustle, increment)
);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY,
    hustle TEXT NOT NULL REFERENCES hustles(id),
    increment TEXT,  -- the increment the proposing worker runs, if any
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',  -- open, taken by an event overseer, then handled or dropped (ADR 0015)
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    hustle TEXT NOT NULL REFERENCES hustles(id),
    increment TEXT,  -- the increment the event overseer works on; it waits meanwhile. NULL for proposals
    kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    proposals TEXT NOT NULL DEFAULT '[]',  -- JSON list of the proposal ids the overseer decides on
    state TEXT NOT NULL DEFAULT 'open',
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY,
    hustle TEXT NOT NULL REFERENCES hustles(id),
    version INTEGER NOT NULL,  -- the plan version it decides on
    baseline INTEGER NOT NULL,  -- the confirmed version it was measured against
    reasons TEXT NOT NULL,  -- JSON list
    waiting TEXT NOT NULL,  -- JSON {increment: "approval" | "provisional"}: what may not start until the yes
    state TEXT NOT NULL DEFAULT 'pending',
    automatic INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL,
    decided REAL
);
"""

AUTO_APPROVE = ("off", "limited", "all")
# Why an event overseer is started (ADR 0015), and how far it got: `open` until it starts and again when the limit
# cut it off, `running`, then `handled` or `failed`.
EVENT_KINDS = ("failed", "too-big", "conflict", "proposal")
EVENT_STATES = ("open", "running", "handled", "failed")
PENDING_EVENTS = ("open", "running")
# A newer plan change supersedes a pending approval: it is judged again as a whole against the same baseline.
APPROVAL_STATES = ("pending", "approved", "rejected", "revoked", "superseded")


@dataclass
class Settings:
    total_points: int = 20
    session_threshold: int = 85  # percent of the 5h window above which nothing new starts (ADR 0008)
    week_target: int = 95  # percent the week should reach at its reset; the target line ends here
    allow_fable: bool = False
    auto_approve: str = "off"
    auto_approve_growth: int = 25  # percent of growth approved automatically in "limited" mode, plus one model step


@dataclass
class Project:
    path: str
    push: bool = False
    copy_excludes: list[str] | None = None
    verify: str | None = None


@dataclass
class Proposal:
    id: int
    hustle: str
    increment: str | None
    author: str
    text: str
    state: str
    created: float


@dataclass
class Event:
    id: int
    hustle: str
    increment: str | None
    kind: str
    detail: str
    proposals: list[int]
    state: str
    created: float


@dataclass
class Approval:
    id: int
    hustle: str
    version: int
    baseline: int
    reasons: list[str]
    waiting: dict[str, str]
    state: str
    automatic: bool
    created: float
    decided: float | None


@dataclass
class PlanVersion:
    version: int
    author: str
    note: str
    created: float
    increments: list[Increment] = field(default_factory=list)


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        # One connection shared by the dashboard, the MCP endpoint and the runner threads; the lock keeps their
        # transactions from interleaving on it.
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._lock = threading.RLock()
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # settings

    def settings(self) -> Settings:
        stored = {row["key"]: json.loads(row["value"]) for row in self.db.execute("SELECT key, value FROM settings")}
        return Settings(**{f.name: stored[f.name] for f in fields(Settings) if f.name in stored})

    def update_settings(self, **changes) -> Settings:
        new = replace(self.settings(), **changes)
        if new.total_points < 1:
            raise ValueError("total_points must be at least 1")
        if not (0 <= new.session_threshold <= 100 and 0 <= new.week_target <= 100):
            raise ValueError("thresholds are percentages between 0 and 100")
        if new.auto_approve not in AUTO_APPROVE:
            raise ValueError(f"auto_approve must be one of {AUTO_APPROVE}")
        if new.auto_approve_growth < 0:
            raise ValueError("auto_approve_growth is a percentage of growth, at least 0")
        with self.db:
            self.db.execute("BEGIN")
            for key, value in changes.items():
                self.db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(getattr(new, key))))
        return new

    # projects

    def project(self, path: str) -> Project | None:
        row = self.db.execute("SELECT * FROM projects WHERE path = ?", (path,)).fetchone()
        if row is None:
            return None
        excludes = json.loads(row["copy_excludes"]) if row["copy_excludes"] is not None else None
        return Project(row["path"], bool(row["push"]), excludes, row["verify"])

    def set_project(self, path: str, *, push: bool | None = None, copy_excludes: list[str] | None = None,
                    verify: str | None = None) -> Project:
        current = self.project(path) or Project(path)
        if push is not None:
            current.push = push
        if copy_excludes is not None:
            current.copy_excludes = copy_excludes
        if verify is not None:
            current.verify = verify or None  # "" clears it
        self.db.execute(
            "INSERT OR REPLACE INTO projects VALUES (?, ?, ?, ?)",
            (path, int(current.push), json.dumps(current.copy_excludes) if current.copy_excludes is not None else None,
             current.verify),
        )
        return current

    # hustles and plans

    def submit(self, project: str, title: str, brief: str, increments: list[Increment], *, author: str,
               push: bool | None = None, verify: str | None = None) -> str:
        """Store a new hustle. It arrives inactive; the first activation confirms it (ADR 0009). `push` and `verify`
        are the project's settings, which `/hustle` settles while planning (ADR 0011); None keeps what is stored."""
        self._check(increments)
        hustle_id = uuid.uuid4().hex[:12]
        with self.db:
            self.db.execute("BEGIN")
            if self.project(project) is None:
                self.db.execute("INSERT INTO projects (path) VALUES (?)", (project,))
            if push is not None or verify is not None:
                self.set_project(project, push=push, verify=verify)
            self.db.execute(
                "INSERT INTO hustles (id, project, title, brief, created) VALUES (?, ?, ?, ?, ?)",
                (hustle_id, project, title, brief, time.time()),
            )
            self._write_version(hustle_id, 1, increments, author, "submitted")
        return hustle_id

    def change_plan(self, hustle_id: str, increments: list[Increment], *, author: str, note: str = "") -> int:
        """Store a new plan version. Progress of increments that keep their id carries over. A change beyond the
        approval bounds of ADR 0015 is stored too, but what it adds waits for the approval (`approvals`)."""
        self._require(hustle_id)
        self._check(increments)
        with self.db:
            self.db.execute("BEGIN")
            version = self._version(hustle_id) + 1
            self._write_version(hustle_id, version, increments, author, note)
            self._judge(hustle_id)
        return version

    def hustle(self, hustle_id: str, version: int | None = None) -> Hustle | None:
        row = self.db.execute("SELECT * FROM hustles WHERE id = ?", (hustle_id,)).fetchone()
        if row is None:
            return None
        current = self._version(hustle_id)
        return Hustle(
            id=row["id"],
            project=row["project"],
            title=row["title"],
            active=bool(row["active"]),
            weight=row["weight"],
            increments=self._increments(hustle_id, version or current),
            brief=row["brief"],
            version=version or current,
            confirmed_version=row["confirmed_version"],
        )

    def hustles(self) -> list[Hustle]:
        ids = [row["id"] for row in self.db.execute("SELECT id FROM hustles ORDER BY created, id")]
        return [self.hustle(i) for i in ids]

    def versions(self, hustle_id: str) -> list[PlanVersion]:
        rows = self.db.execute(
            "SELECT * FROM plan_versions WHERE hustle = ? ORDER BY version", (hustle_id,)
        ).fetchall()
        return [
            PlanVersion(r["version"], r["author"], r["note"], r["created"], self._increments(hustle_id, r["version"]))
            for r in rows
        ]

    def set_active(self, hustle_id: str, active: bool) -> None:
        self._require(hustle_id)
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("UPDATE hustles SET active = ? WHERE id = ?", (int(active), hustle_id))
            if active:
                self.db.execute(
                    "UPDATE hustles SET confirmed_version = ? WHERE id = ? AND confirmed_version IS NULL",
                    (self._version(hustle_id), hustle_id),
                )

    def confirm(self, hustle_id: str) -> None:
        """Make the current plan the baseline that growth is measured against: the user's yes to it as a whole,
        which also settles a pending approval (ADR 0015)."""
        self._require(hustle_id)
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("UPDATE hustles SET confirmed_version = ? WHERE id = ?",
                            (self._version(hustle_id), hustle_id))
            self.db.execute("UPDATE approvals SET state = 'approved', decided = ? WHERE hustle = ? AND state = 'pending'",
                            (time.time(), hustle_id))

    def set_weight(self, hustle_id: str, weight: int) -> None:
        self._require(hustle_id)
        if weight < 1:
            raise ValueError("a weight is at least 1")
        self.db.execute("UPDATE hustles SET weight = ? WHERE id = ?", (weight, hustle_id))

    # progress

    def set_state(self, hustle_id: str, increment: str, state: str) -> None:
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        self._progress(hustle_id, increment, "state = ?", state)

    def count_attempt(self, hustle_id: str, increment: str) -> None:
        self._progress(hustle_id, increment, "attempts = attempts + 1")

    def note_peak_context(self, hustle_id: str, increment: str, tokens: int) -> None:
        self._progress(hustle_id, increment, "peak_context = MAX(peak_context, ?)", tokens)

    def leave_note(self, hustle_id: str, increment: str, note: str | None) -> None:
        self._progress(hustle_id, increment, "note = ?", note)

    def take_note(self, hustle_id: str, increment: str) -> str | None:
        """The note for the increment's next session; it is handed over once."""
        row = self.db.execute("SELECT note FROM progress WHERE hustle = ? AND increment = ?",
                              (hustle_id, increment)).fetchone()
        if row is None:
            raise KeyError(f"unknown increment {increment!r} in hustle {hustle_id!r}")
        self._progress(hustle_id, increment, "note = NULL")
        return row["note"]

    # proposals

    def propose(self, hustle_id: str, text: str, *, author: str, increment: str | None = None) -> int:
        """A worker's plan change request; it changes nothing until an overseer acts on it (ADR 0015)."""
        self._require(hustle_id)
        if not text.strip():
            raise ValueError("a proposal needs a text")
        cursor = self.db.execute(
            "INSERT INTO proposals (hustle, increment, author, text, created) VALUES (?, ?, ?, ?, ?)",
            (hustle_id, increment, author, text, time.time()),
        )
        return cursor.lastrowid

    def proposals(self, hustle_id: str, *, state: str | None = None) -> list[Proposal]:
        rows = self.db.execute(
            "SELECT * FROM proposals WHERE hustle = ? AND (? IS NULL OR state = ?) ORDER BY id",
            (hustle_id, state, state),
        ).fetchall()
        return [Proposal(**dict(r)) for r in rows]

    # events

    def open_event(self, hustle_id: str, kind: str, *, increment: str | None = None, detail: str = "",
                   proposals: list[int] = ()) -> int:
        """Ask for an event overseer. Until it has handled the event, the increment it is about waits."""
        self._require(hustle_id)
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind!r}")
        with self.db:
            self.db.execute("BEGIN")
            cursor = self.db.execute(
                "INSERT INTO events (hustle, increment, kind, detail, proposals, created) VALUES (?, ?, ?, ?, ?, ?)",
                (hustle_id, increment, kind, detail, json.dumps(list(proposals)), time.time()),
            )
            self.db.executemany("UPDATE proposals SET state = 'taken' WHERE id = ? AND state = 'open'",
                                [(p,) for p in proposals])
        return cursor.lastrowid

    def events(self, hustle_id: str | None = None, *, states: tuple[str, ...] | None = None) -> list[Event]:
        rows = self.db.execute(
            "SELECT * FROM events WHERE (? IS NULL OR hustle = ?) ORDER BY id", (hustle_id, hustle_id)
        ).fetchall()
        found = [Event(**{**dict(r), "proposals": json.loads(r["proposals"])}) for r in rows]
        return [e for e in found if states is None or e.state in states]

    def event(self, event_id: int) -> Event:
        row = self.db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown event {event_id}")
        return Event(**{**dict(row), "proposals": json.loads(row["proposals"])})

    def set_event_state(self, event_id: int, state: str) -> None:
        """Move an event on. Once it is over, its proposals are decided (or dropped with a failed overseer), and its
        increment starts over with fresh attempts: the overseer changed it or found nothing to change."""
        if state not in EVENT_STATES:
            raise ValueError(f"unknown event state {state!r}")
        event = self.event(event_id)
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("UPDATE events SET state = ? WHERE id = ?", (state, event_id))
            if state in PENDING_EVENTS:
                return
            decided = "handled" if state == "handled" else "dropped"
            self.db.executemany("UPDATE proposals SET state = ? WHERE id = ?", [(decided, p) for p in event.proposals])
            if event.increment is not None:
                self.db.execute("UPDATE progress SET attempts = 0 WHERE hustle = ? AND increment = ?",
                                (event.hustle, event.increment))

    # approvals (dashboard only, ADR 0014)

    def approvals(self, hustle_id: str | None = None, *, states: tuple[str, ...] | None = None) -> list[Approval]:
        rows = self.db.execute(
            "SELECT * FROM approvals WHERE (? IS NULL OR hustle = ?) ORDER BY id", (hustle_id, hustle_id)
        ).fetchall()
        found = [self._approval(r) for r in rows]
        return [a for a in found if states is None or a.state in states]

    def approval(self, approval_id: int) -> Approval:
        row = self.db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown approval {approval_id}")
        return self._approval(row)

    def approve(self, approval_id: int) -> None:
        self.confirm(self._pending(approval_id).hustle)

    def reject(self, approval_id: int) -> None:
        """No to the change: what waited for it is blocked until the user edits the plan. Reverting the model
        instead would only hand the increment back to the model that already failed it twice."""
        approval = self._pending(approval_id)
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("UPDATE approvals SET state = 'rejected', decided = ? WHERE id = ?",
                            (time.time(), approval_id))
            self.db.executemany(
                "UPDATE progress SET state = 'blocked' WHERE hustle = ? AND increment = ? AND state = 'open'",
                [(approval.hustle, i) for i in approval.waiting],
            )

    def revoke(self, approval_id: int) -> None:
        """Take back an automatic approval: the baseline moves back and the plan is judged again, now for the user.
        Only the latest confirmation can be taken back; what already ran stays done."""
        approval = self.approval(approval_id)
        if approval.state != "approved" or not approval.automatic:
            raise ValueError("only automatic approvals can be revoked")
        if self.hustle(approval.hustle).confirmed_version != approval.version:
            raise ValueError("the plan was confirmed again since; only the latest approval can be revoked")
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("UPDATE approvals SET state = 'revoked', decided = ? WHERE id = ?",
                            (time.time(), approval_id))
            self.db.execute("UPDATE hustles SET confirmed_version = ? WHERE id = ?",
                            (approval.baseline, approval.hustle))
            self._judge(approval.hustle, automatic=False)

    # internals

    def _judge(self, hustle_id: str, *, automatic: bool = True) -> None:
        # Called inside the plan change's transaction. Before the first activation nothing is judged: the
        # activation confirms whatever the plan is then.
        self.db.execute("UPDATE approvals SET state = 'superseded', decided = ? WHERE hustle = ? AND state = 'pending'",
                        (time.time(), hustle_id))
        hustle = self.hustle(hustle_id)
        if hustle.confirmed_version is None:
            return
        settings = self.settings()
        judgement = judging.judge(self._increments(hustle_id, hustle.confirmed_version), hustle.increments,
                                  judging.share(hustle, self.hustles(), settings.total_points))
        if not judgement.needed:
            return
        auto = automatic and judging.automatic(judgement, settings.auto_approve, settings.auto_approve_growth)
        now = time.time()
        self.db.execute(
            "INSERT INTO approvals (hustle, version, baseline, reasons, waiting, state, automatic, created, decided)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (hustle_id, hustle.version, hustle.confirmed_version, json.dumps(judgement.reasons()),
             json.dumps(judgement.waiting()), "approved" if auto else "pending", int(auto), now, now if auto else None),
        )
        if auto:
            self.db.execute("UPDATE hustles SET confirmed_version = ? WHERE id = ?", (hustle.version, hustle_id))

    def _pending(self, approval_id: int) -> Approval:
        approval = self.approval(approval_id)
        if approval.state != "pending":
            raise ValueError(f"approval {approval_id} is {approval.state}, not pending")
        return approval

    @staticmethod
    def _approval(row: sqlite3.Row) -> Approval:
        return Approval(**{**dict(row), "reasons": json.loads(row["reasons"]), "waiting": json.loads(row["waiting"]),
                           "automatic": bool(row["automatic"])})

    def _check(self, increments: list[Increment]) -> None:
        settings = self.settings()
        plan.check(increments, allow_fable=settings.allow_fable, total_points=settings.total_points)

    def _require(self, hustle_id: str) -> None:
        if self.db.execute("SELECT 1 FROM hustles WHERE id = ?", (hustle_id,)).fetchone() is None:
            raise KeyError(f"unknown hustle {hustle_id!r}")

    def _version(self, hustle_id: str) -> int:
        row = self.db.execute("SELECT MAX(version) FROM plan_versions WHERE hustle = ?", (hustle_id,)).fetchone()
        return row[0] or 0

    def _write_version(self, hustle_id: str, version: int, increments: list[Increment], author: str, note: str) -> None:
        self.db.execute(
            "INSERT INTO plan_versions VALUES (?, ?, ?, ?, ?)", (hustle_id, version, author, note, time.time())
        )
        for position, inc in enumerate(increments):
            self.db.execute(
                "INSERT INTO plan_increments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (hustle_id, version, position, inc.id, inc.title, inc.prompt, json.dumps(inc.after), inc.model, inc.mode),
            )
            self.db.execute("INSERT OR IGNORE INTO progress (hustle, increment) VALUES (?, ?)", (hustle_id, inc.id))

    def _increments(self, hustle_id: str, version: int) -> list[Increment]:
        rows = self.db.execute(
            """SELECT p.*, g.state, g.attempts, g.peak_context,
                      EXISTS (SELECT 1 FROM events e WHERE e.hustle = p.hustle AND e.increment = p.id
                              AND e.state IN ('open', 'running')) AS overseen
               FROM plan_increments p
               JOIN progress g ON g.hustle = p.hustle AND g.increment = p.id
               WHERE p.hustle = ? AND p.version = ? ORDER BY p.position""",
            (hustle_id, version),
        ).fetchall()
        pending = self.db.execute("SELECT waiting FROM approvals WHERE hustle = ? AND state = 'pending'",
                                  (hustle_id,)).fetchone()
        waiting = json.loads(pending["waiting"]) if pending else {}
        return [
            Increment(
                id=r["id"],
                title=r["title"],
                model=r["model"],
                instructions=[],
                after=json.loads(r["after"]),
                state=r["state"],
                prompt=r["prompt"],
                mode=r["mode"],
                attempts=r["attempts"],
                peak_context=r["peak_context"],
                held="overseer" if r["overseen"] else waiting.get(r["id"], ""),
            )
            for r in rows
        ]

    def _progress(self, hustle_id: str, increment: str, assignment: str, *args) -> None:
        cursor = self.db.execute(
            f"UPDATE progress SET {assignment} WHERE hustle = ? AND increment = ?", (*args, hustle_id, increment)
        )
        if cursor.rowcount == 0:
            raise KeyError(f"unknown increment {increment!r} in hustle {hustle_id!r}")


def _locked(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


for _name, _member in list(vars(Store).items()):
    if callable(_member) and not _name.startswith("_"):
        setattr(Store, _name, _locked(_member))

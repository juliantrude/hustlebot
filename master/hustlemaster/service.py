"""The hustlemaster's service loop (ADR 0008): one process runs the store, the MCP endpoint, the usage reader, the
web app and every hustlebot of the machine.

Each pass reaps the sessions that ended, gathers open proposals into events, and starts what `events.due` and
`scheduler.choose` say may start now. A finished session's points are free at once; it is settled on a thread of its
own, because its merge runs the project's check, which may take long and must not hold back other hustles' starts.

Sessions outlive the process: a restart leaves them running. The next process finds their worktrees
(`merge.orphans`), waits for a session that still runs and settles it like one of its own, settles a finished one,
and lets one that was cut off start again in its worktree. An event overseer that was running is ended and its event
opened again, so its successor sees the plan as it is now.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import events, merge, runner, scheduler, worktree
from .access import Caller, Tokens
from .budget import Budget
from .live import Live
from .mcp_server import Endpoint
from .runner import PLUGIN_DIR, Hustlebot, Outcome, Runner
from .store import PENDING_EVENTS, Project, Store
from .usage import Usage, UsageError
from .worktree import Worktree

log = logging.getLogger("hustlemaster")

PASS_SECONDS = 5.0
USAGE_SECONDS = 600.0  # the `/usage` screen costs a short interactive session; own accounting fills the gaps

RESTARTED_NOTE = ("The previous session of this increment was cut off when the hustlemaster restarted, not for "
                  "anything it did. Its work is in this worktree; check what is there and continue from it.")


class Adopted:
    """A session a previous hustlemaster process started. Its MCP token died with that process, so its calls to the
    hustlemaster are refused; its work and its report are still its own."""

    def __init__(self, run_dir: Path) -> None:
        self.dir = run_dir

    def running(self) -> bool:
        return merge.alive(self.dir)

    def wait(self, timeout: float | None = None) -> Outcome:
        return runner.read_run(self.dir)


@dataclass
class Job:
    hustle: str
    key: str  # the increment's id, or `event-<id>` for an event overseer
    model: str
    mode: str
    repo: str
    worktree: Worktree
    bot: Hustlebot | Adopted
    started: float
    event: int | None = None


class Service:
    """`home` keeps the runs (prompts, streams) and the worktrees; `claude`, `env` and `plugin_dir` are what the
    hustlebots are started with."""

    def __init__(self, store: Store, home: Path | str, *, claude: str = "claude", env: dict[str, str] | None = None,
                 plugin_dir: Path | str = PLUGIN_DIR, usage: Usage | None = None, mcp_port: int = 0,
                 interactive: str | None = None, usage_cwd: Path | str | None = None) -> None:
        self.store = store
        self.home = Path(home).resolve()
        self.worktrees = self.home / "worktrees"
        self.tokens = Tokens(interactive)
        self.endpoint = Endpoint(store, self.tokens, mcp_port)
        self.usage = usage or Usage()
        self.budget = Budget(self.usage)
        self.live = Live(store, self.usage)
        self.runner = Runner(self.endpoint.mcp_config(), self.tokens, plugin_dir, self.home / "runs", claude=claude,
                             env=env)
        self.claude = claude
        self.usage_cwd = usage_cwd
        self.jobs: dict[tuple[str, str], Job] = {}
        self.retries: dict[tuple[str, str], tuple[Job, Outcome]] = {}  # done, the merge waits for the user's `main`
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()

    # running

    def start(self, every: float = PASS_SECONDS, usage_every: float | None = USAGE_SECONDS) -> Service:
        self.endpoint.start()
        self.recover()
        self.live.run_forever()
        self._forever(self.step, every)
        if usage_every:
            self._forever(self.refresh_usage, usage_every)
        return self

    def stop(self) -> None:
        """Stop starting; running sessions go on and the next process adopts them."""
        self._stop.set()
        self.drain()
        self.endpoint.stop()

    def _forever(self, work, every: float) -> None:
        def loop() -> None:
            while not self._stop.is_set():
                try:
                    work()
                except Exception:
                    log.exception("%s failed", work.__name__)
                self._stop.wait(every)

        threading.Thread(target=loop, daemon=True, name=work.__name__).start()

    def drain(self) -> None:
        """Wait for the settling threads; tests call it after a pass to see its whole effect."""
        while True:
            with self._lock:
                threads = [t for t in self._threads if t.is_alive()]
                self._threads = threads
            if not threads:
                return
            for t in threads:
                t.join()

    def refresh_usage(self) -> None:
        cwd = self.usage_cwd or next((h.project for h in self.store.hustles() if Path(h.project).is_dir()), None)
        if cwd is None:
            return  # no folder known to be trusted; the stream readings of the sessions still come in
        try:
            # the unit's PATH may lack the user's shell additions; the sessions' `claude` is known to work
            self.usage.refresh(cwd, command=(self.claude,))
        except (UsageError, OSError) as err:
            log.warning("usage screen not read: %s", err)

    # one pass

    def step(self, now: datetime | None = None) -> scheduler.Decision:
        self._reap()
        with self._lock:
            retries, self.retries = list(self.retries.values()), {}
        for job, outcome in retries:
            self._spawn(self._merge, job, outcome)
        for hustle in self.store.hustles():
            events.sync_proposals(self.store, hustle.id)

        hustles, settings = self.store.hustles(), self.store.settings()
        sessions = self.live.sessions()
        due = events.due(hustles, self.store.events(states=PENDING_EVENTS), sessions, settings)
        decision = scheduler.choose(hustles, settings.total_points, sessions=sessions, overseers=due,
                                    budget=self.budget, settings=settings, now=now)
        for start in decision.starts:
            self._start(start, settings)
        return decision

    def _reap(self) -> None:
        for key, job in list(self.jobs.items()):
            if job.bot.running():
                continue
            del self.jobs[key]
            outcome = job.bot.wait()
            self.live.finished(job.hustle, job.key, outcome.status)
            self._spawn(self._settle, job, outcome)

    def _spawn(self, work, *args) -> None:
        def run() -> None:
            try:
                work(*args)
            except Exception:
                log.exception("settling %s failed", args[0].key if args else "")

        thread = threading.Thread(target=run, daemon=True)
        with self._lock:
            self._threads.append(thread)
        thread.start()

    def _start(self, start: scheduler.Start, settings) -> None:
        hustle = self.store.hustle(start.hustle)
        project = self.store.project(hustle.project) or Project(hustle.project)
        eid = events.event_id(start.increment)
        event = self.store.event(eid) if eid is not None else None
        if event is not None:
            inc = events.overseer_increment(event, hustle, start.model)
            # it works where the affected increment's work is; a proposal's overseer gets its own place at `main`
            place = event.increment or inc.id
        else:
            inc = next(i for i in hustle.increments if i.id == start.increment)
            place = inc.id
        try:
            wt = worktree.create(hustle.project, self.worktrees, hustle.id, place, excludes=project.copy_excludes)
            note = None
            if event is None:
                note = "\n\n".join(filter(None, [self.budget.resume(hustle.id, inc.id),
                                                 self.store.take_note(hustle.id, inc.id)])) or None
            bot = self.runner.start(hustle, inc, wt.path, allow_fable=settings.allow_fable, note=note)
        except Exception as err:
            # a missing repo, a model switched off since: retrying each pass would fail the same way
            log.warning("could not start %s/%s: %s", hustle.id, start.increment, err)
            failed = Outcome("failed", None, f"could not start: {err}", None, {}, 0)
            if event is not None:
                events.close(self.store, event, failed)
            else:
                self.store.leave_note(hustle.id, inc.id, failed.error)
                self.store.set_state(hustle.id, inc.id, "blocked")
            return
        if event is not None:
            self.store.set_event_state(event.id, "running")
        else:
            self.store.set_state(hustle.id, inc.id, "running")
        self._track(Job(hustle.id, start.increment, start.model, inc.mode, hustle.project, wt, bot, time.time(),
                        eid))

    def _track(self, job: Job) -> None:
        self.jobs[(job.hustle, job.key)] = job
        self.live.started(job.hustle, job.key, job.model)

    # after a session

    def _settle(self, job: Job, outcome: Outcome) -> None:
        parked = self.budget.finished(job.hustle, job.key, job.model, outcome) is not None
        if job.event is not None:
            event = self.store.event(job.event)
            events.close(self.store, event, outcome, parked=parked)
            if event.increment is None and not parked:
                worktree.remove(job.repo, job.worktree)
            return
        self.store.note_peak_context(job.hustle, job.key, outcome.peak_context)
        if job.mode == "worker":
            self._keep_proposals(job, outcome)
        self._merge(job, outcome, parked)

    def _merge(self, job: Job, outcome: Outcome, parked: bool = False) -> None:
        inc = next((i for i in self.store.hustle(job.hustle).increments if i.id == job.key), None)
        if inc is None:
            log.warning("increment %s/%s left the plan while it ran; its worktree stays", job.hustle, job.key)
            return
        step = events.settle(inc.attempts, outcome, parked=parked)
        if step.action == events.MERGE:
            if job.mode == "overseer" and not _committed(job.worktree):
                # a planned overseer changes the plan, not the code; done without a commit is its normal end
                worktree.remove(job.repo, job.worktree)
                step = events.Step(events.DONE)
            else:
                project = self.store.project(job.repo) or Project(job.repo)
                result = merge.merge(job.repo, job.worktree, verify=project.verify, push=project.push)
                if result.push_error:
                    log.warning("push of %s failed: %s", job.repo, result.push_error)
                step = events.settle(inc.attempts, outcome, result)
        if step.action == events.RETRY:
            with self._lock:
                self.retries[(job.hustle, job.key)] = (job, outcome)
        events.record(self.store, job.hustle, job.key, step)

    def _keep_proposals(self, job: Job, outcome: Outcome) -> None:
        """The report repeats what the worker proposed over MCP; only if none of its proposals arrived that way —
        its MCP calls failed, or it forgot — are the report's stored."""
        if outcome.report is None or outcome.error is not None or not outcome.report.proposals:
            return
        author = Caller(job.mode, job.hustle, job.key).author
        if any(p.author == author and p.created >= job.started for p in self.store.proposals(job.hustle)):
            return
        for text in outcome.report.proposals:
            self.store.propose(job.hustle, text, author=author, increment=job.key)

    # after a restart

    def recover(self) -> None:
        for event in self.store.events(states=("running",)):
            run = runner.last_run(self.runner.runs, event.hustle, events.key(event.id))
            if run is not None and merge.alive(run):
                _end(run)
            self.store.set_event_state(event.id, "open")

        hustles = self.store.hustles()
        found: set[tuple[str, str]] = set()
        for repo in sorted({h.project for h in hustles}):
            if not Path(repo).is_dir():
                continue
            for orphan in merge.orphans(repo, hustles, self.runner.runs):
                found.add((orphan.hustle, orphan.increment))
                inc = next(i for h in hustles if h.id == orphan.hustle for i in h.increments if i.id == orphan.increment)
                if inc.state != "running":
                    continue  # settled before the restart: its worktree waits for the next attempt
                if orphan.action == merge.RESUME and not _reported(orphan.run):
                    # cut off by the restart, or never started: no fault of the increment, no attempt
                    self.store.leave_note(orphan.hustle, inc.id, RESTARTED_NOTE)
                    self.store.set_state(orphan.hustle, inc.id, "open")
                    continue
                # still running, or ended with a result nobody settled: settled like a session of this process
                self._track(Job(orphan.hustle, inc.id, inc.model, inc.mode, repo, orphan.worktree,
                                Adopted(orphan.run), time.time()))
        for h in hustles:
            for inc in h.running():
                if (h.id, inc.id) not in found:
                    # it never got a worktree, or lost it; nothing of it is left to continue from
                    self.store.set_state(h.id, inc.id, "open")


def _reported(run: Path | None) -> bool:
    # a result event carries the session id; a stream without one was cut off
    return run is not None and runner.read_run(run).session_id is not None


def _committed(wt: Worktree) -> bool:
    return subprocess.run(["git", "-C", str(wt.path), "merge-base", "--is-ancestor", "HEAD", merge.MAIN],
                          capture_output=True).returncode != 0


def _end(run_dir: Path) -> None:
    # the session leads its own process group (`start_new_session`), so its children go with it
    try:
        os.killpg(int((run_dir / "pid").read_text()), signal.SIGTERM)
    except (OSError, ValueError):
        pass

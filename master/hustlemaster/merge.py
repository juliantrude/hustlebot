"""Bringing a verified increment into `main` (ADR 0011): one commit, rebased, verified again, fast-forwarded.

Only the hustlemaster moves `main`, one merge per repository at a time, and only by fast-forward: `main` never
gets a commit nobody verified on top of it. The re-verification is the project's own check command
(`Project.verify`), run in the worktree after the rebase. Without one the clean rebase is all that is checked,
and the acceptance overseer is the backstop.

A merge that cannot happen right now because of the user — local changes in the main checkout in the way, or a
commit of theirs landing during the check — is no fault of the increment: it is retried and costs no attempt.
"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import runner, worktree
from .model import Hustle
from .worktree import Worktree, git

MAIN = "main"
VERIFY_TIMEOUT = 1800
OUTPUT_TAIL = 4000

# what a merge result means for the increment (ADR 0015): an attempt, or a retry that costs nothing
MERGED, NO_COMMIT, CONFLICT, VERIFY_FAILED, MAIN_BUSY = "merged", "no-commit", "conflict", "verify-failed", "main-busy"
COUNTS_AS_ATTEMPT = {NO_COMMIT, CONFLICT, VERIFY_FAILED}

_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


@dataclass(frozen=True)
class Merge:
    status: str
    commit: str | None = None  # the increment's commit on `main`, when merged
    detail: str = ""
    conflicts: list[str] = field(default_factory=list)
    pushed: bool | None = None  # None: the project does not push
    push_error: str | None = None

    def note(self) -> str | None:
        """What the same increment's next session must know to repair the merge; None if it needs nothing."""
        if self.status == CONFLICT:
            files = "\n".join(f"- `{f}`" for f in self.conflicts)
            return (f"Your commit no longer applies to `{MAIN}`; it conflicts in:\n\n{files}\n\nRun `git rebase {MAIN}` "
                    f"in this worktree, resolve the conflicts so both sides' intent survives, verify again, and leave "
                    f"exactly one commit on top of `{MAIN}`.")
        if self.status == VERIFY_FAILED:
            return (f"Your commit was rebased onto the current `{MAIN}`, and the project's check failed there:\n\n"
                    f"```\n{self.detail}\n```\n\nMake the check pass on top of `{MAIN}`, keeping exactly one commit.")
        if self.status == NO_COMMIT:
            return f"The previous attempt reported done but left no commit on top of `{MAIN}`. Commit the work."
        return None


def _lock(repo: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(repo, threading.Lock())


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _created_at(repo: Path, branch: str) -> str | None:
    # the branch's oldest reflog entry is where it was created; rebases and squashes only add newer ones
    out = _run(repo, "reflog", "show", "--format=%H", f"refs/heads/{branch}")
    lines = out.stdout.split() if out.returncode == 0 else []
    return lines[-1] if lines else None


def _main_checkout(repo: Path) -> Path | None:
    path = None
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
        elif line == f"branch refs/heads/{MAIN}":
            return path
    return None


def _squash(wt: Worktree, base: str) -> None:
    """Fold several commits into one; the plan counts one commit per increment and a sloppy session should not
    cost an attempt for it."""
    messages = git(wt.path, "log", "--reverse", "--format=%B", f"{base}..HEAD").strip()
    git(wt.path, "reset", "-q", "--soft", base)
    git(wt.path, "commit", "-q", "--no-verify", "-m", messages)


def _verify(wt: Worktree, command: str, timeout: float) -> str | None:
    """The check's output tail if it failed, None if it passed."""
    try:
        done = subprocess.run(command, shell=True, cwd=wt.path, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, start_new_session=True)
    except subprocess.TimeoutExpired:
        return f"`{command}` did not finish within {timeout:.0f} s"
    if done.returncode == 0:
        return None
    return f"`{command}` exited {done.returncode}\n{(done.stdout + done.stderr)[-OUTPUT_TAIL:]}"


def _fast_forward(repo: Path, old: str, new: str) -> str | None:
    """Move `main` from `old` to `new`; why not, or None. Where `main` is checked out its files move with it, and
    git refuses rather than overwrite the user's local changes."""
    checkout = _main_checkout(repo)
    if checkout is None:
        out = _run(repo, "update-ref", f"refs/heads/{MAIN}", new, old)
    elif _run(checkout, "rev-parse", MAIN).stdout.strip() != old:
        return f"`{MAIN}` moved during the merge"
    else:
        out = _run(checkout, "merge", "-q", "--ff-only", new)
    return None if out.returncode == 0 else (out.stderr or out.stdout).strip()


def merge(repo: Path | str, wt: Worktree, *, verify: str | None = None, push: bool = False, remote: str = "origin",
          verify_timeout: float = VERIFY_TIMEOUT) -> Merge:
    """Merge the increment's worktree branch into `main` and remove the worktree. Anything but MERGED leaves the
    worktree as it is, for the retry or the next attempt."""
    repo = Path(repo).resolve()
    with _lock(repo):
        main = git(repo, "rev-parse", MAIN).strip()
        head = git(wt.path, "rev-parse", "HEAD").strip()
        if _run(repo, "merge-base", "--is-ancestor", head, main).returncode == 0:
            created = _created_at(repo, wt.branch)
            if created is not None and head != created:
                # a restart between the fast-forward and the removal: the work is in `main` already
                worktree.remove(repo, wt)
                return Merge(MERGED, head, f"already in `{MAIN}`")
            return Merge(NO_COMMIT, detail=f"no commit on top of `{MAIN}`")

        # what goes into `main` is the commit; leftovers outside it must not make the check pass or fail.
        # Ignored files stay: they are the copied local config and the project's installed dependencies.
        git(wt.path, "reset", "-q", "--hard")
        git(wt.path, "clean", "-q", "-fd")
        base = git(wt.path, "merge-base", MAIN, "HEAD").strip()
        if len(git(wt.path, "rev-list", f"{base}..HEAD").split()) > 1:
            _squash(wt, base)
        if base != main:
            rebase = _run(wt.path, "rebase", "-q", MAIN)
            if rebase.returncode != 0:
                conflicts = _run(wt.path, "diff", "--name-only", "--diff-filter=U").stdout.split()
                _run(wt.path, "rebase", "--abort")
                return Merge(CONFLICT, detail=(rebase.stderr or rebase.stdout).strip()[-OUTPUT_TAIL:],
                             conflicts=conflicts)

        if verify:
            failure = _verify(wt, verify, verify_timeout)
            if failure is not None:
                return Merge(VERIFY_FAILED, detail=failure)

        commit = git(wt.path, "rev-parse", "HEAD").strip()
        refused = _fast_forward(repo, main, commit)
        if refused is not None:
            return Merge(MAIN_BUSY, detail=refused)
        worktree.remove(repo, wt)

        pushed, push_error = None, None
        if push:
            out = _run(repo, "push", "-q", remote, f"{MAIN}:{MAIN}")
            pushed = out.returncode == 0
            push_error = None if pushed else (out.stderr or out.stdout).strip()
        return Merge(MERGED, commit, pushed=pushed, push_error=push_error)


# after a restart

MERGE, RESUME, RUNNING = "merge", "resume", "running"


@dataclass(frozen=True)
class Orphan:
    hustle: str
    increment: str
    worktree: Worktree
    action: str  # MERGE: its last session reported done; RESUME: start the increment again here; RUNNING: wait
    run: Path | None  # the last run's directory


def alive(run_dir: Path) -> bool:
    """Whether the run's session still runs; a hustlemaster restart need not end its children."""
    try:
        pid = int((run_dir / "pid").read_text())
    except (OSError, ValueError):
        return False
    if Path("/proc/self").exists():
        # the pid may have been reused; the session's command line names its run's MCP config
        try:
            return str(run_dir / "mcp.json") in Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
        except OSError:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def orphans(repo: Path | str, hustles: list[Hustle], runs: Path | str) -> list[Orphan]:
    """The increments of `hustles` that still have a worktree in `repo`, and what to do with each.

    Worktrees of increments no hustle knows any more are left out; they are not the hustlemaster's to judge.
    """
    by_branch = {worktree.branch_name(h.id, i.id): (h.id, i.id) for h in hustles for i in h.increments}
    found = []
    for wt in worktree.listed(repo):
        if wt.branch not in by_branch:
            continue
        hustle, increment = by_branch[wt.branch]
        run = runner.last_run(runs, hustle, increment)
        if run is not None and alive(run):
            action = RUNNING
        elif run is not None and runner.read_run(run).status == "done":
            action = MERGE
        else:
            action = RESUME
        found.append(Orphan(hustle, increment, wt, action, run))
    return found

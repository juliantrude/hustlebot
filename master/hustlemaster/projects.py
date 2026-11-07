"""Projects on the hustlemaster's host (ADR 0016): one repository per project, under one root, with at most one
remote.

The user plans elsewhere and hands the state over in one of two ways: through the project's remote, which the
hustlemaster fetches, or by pushing straight into the repository here — which is why it accepts pushes to its
checked-out `main` (`receive.denyCurrentBranch updateInstead`). Results leave the same way: pushed to the remote
if the project says so, or fetched by the user from here.

A push never blocks a hustle. The increment is done once `main` here has moved; pushing is a step after it, retried
until it goes through.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

# a project, optionally in one group folder ("final-lift/final-lift-api"): projects that belong together sit together
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}(/[A-Za-z0-9][A-Za-z0-9._-]{0,63})?")
PUSH_MODES = ("off", "branch", "main")
MAIN = "main"
REMOTE = "origin"
BRANCH_PREFIX = "hustle/"
GIT_TIMEOUT = 300

# never wait for a password or a host key question nobody is there to answer
_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}


class ProjectError(ValueError):
    pass


def _git(cwd: Path | None, *args: str) -> subprocess.CompletedProcess:
    where = ["-C", str(cwd)] if cwd is not None else []
    try:
        return subprocess.run(["git", *where, *args], capture_output=True, text=True, env=_ENV, timeout=GIT_TIMEOUT,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"git {args[0]} did not finish within {GIT_TIMEOUT} s")


def _why(out: subprocess.CompletedProcess) -> str:
    return (out.stderr or out.stdout).strip()[-2000:]


def path_for(root: Path | str, name: str) -> Path:
    if not NAME.fullmatch(name):
        raise ProjectError(f"project name {name!r}: letters, digits, '.', '_' and '-', at most 64, optionally "
                           f"behind one group folder (group/name)")
    return Path(root).expanduser().resolve() / name


def prepare(path: Path, remote: str | None) -> None:
    """Make sure the repository exists, takes pushes to `main`, and has `remote` as its only remote (None: none)."""
    if not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        out = _git(None, "clone", "-q", remote, str(path)) if remote else _git(None, "init", "-q", "-b", MAIN, str(path))
        if out.returncode != 0:
            raise ProjectError(f"could not {'clone ' + remote if remote else 'create ' + str(path)}: {_why(out)}")
    _git(path, "config", "receive.denyCurrentBranch", "updateInstead")
    current = _git(path, "remote", "get-url", REMOTE)
    if remote is None:
        if current.returncode == 0:
            _git(path, "remote", "remove", REMOTE)
    elif current.returncode != 0:
        _git(path, "remote", "add", REMOTE, remote)
    elif current.stdout.strip() != remote:
        _git(path, "remote", "set-url", REMOTE, remote)


def sync(path: Path) -> str | None:
    """Bring the remote's `main` into `main` here where that is a fast-forward. Why not, or None. Without a remote
    there is nothing to do: the user pushes here."""
    if _git(path, "remote", "get-url", REMOTE).returncode != 0:
        return None
    out = _git(path, "fetch", "-q", REMOTE)
    if out.returncode != 0:
        return f"fetch from {REMOTE} failed: {_why(out)}"
    theirs = f"refs/remotes/{REMOTE}/{MAIN}"
    if _git(path, "rev-parse", "--verify", "-q", theirs).returncode != 0:
        return None  # an empty remote
    if _git(path, "rev-parse", "--verify", "-q", MAIN).returncode != 0:
        out = _git(path, "checkout", "-q", "-b", MAIN, theirs)
        return None if out.returncode == 0 else _why(out)
    if _git(path, "merge-base", "--is-ancestor", theirs, MAIN).returncode == 0:
        return None  # nothing new there
    if _git(path, "merge-base", "--is-ancestor", MAIN, theirs).returncode != 0:
        return f"`{MAIN}` here and on {REMOTE} have diverged; `{MAIN}` here stays as it is"
    # `main` is checked out here; a fast-forward merge moves its files with it
    out = _git(path, "merge", "-q", "--ff-only", theirs)
    return None if out.returncode == 0 else _why(out)


def contains(path: Path, commit: str) -> bool:
    """Whether `commit` is in `main` here, i.e. the state a plan was made against has arrived."""
    if _git(path, "rev-parse", "--verify", "-q", f"{commit}^{{commit}}").returncode != 0:
        return False
    return _git(path, "merge-base", "--is-ancestor", commit, MAIN).returncode == 0


def missing(path: Path, required: list[str]) -> list[str]:
    """The required ignored files (an `.env`, a local config) that are not in the repository here."""
    return [f for f in required if not (path / f).exists()]


def branch(hustle_id: str) -> str:
    return BRANCH_PREFIX + hustle_id


def push(path: Path, mode: str, hustle_id: str) -> str | None:
    """Push `main` here to the remote as the project says; why it failed, or None. "branch" pushes to the hustle's
    own branch, "main" to `main`; "off" and a project without a remote push nothing."""
    if mode == "off" or _git(path, "remote", "get-url", REMOTE).returncode != 0:
        return None
    target = MAIN if mode == "main" else branch(hustle_id)
    out = _git(path, "push", "-q", REMOTE, f"{MAIN}:refs/heads/{target}")
    return None if out.returncode == 0 else _why(out)


def head(path: Path) -> str | None:
    out = _git(path, "rev-parse", "-q", "--verify", MAIN)
    return out.stdout.strip() if out.returncode == 0 else None

"""Worktrees for hustlebots (ADR 0011): one per increment, on a short-lived branch, removed after the merge.

A worktree holds only what git tracks, so the main checkout's gitignored files (`.env`, local config, fixtures
the developer keeps out of git) are copied in — without build output, installed dependencies and large files,
which are slow to copy and rebuilt by the project's own tooling. Claude Code already loads the main checkout's
`.claude/settings.local.json` in a worktree (`prototypes/worktree_session.sh`); a copy would load its hooks twice.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

BRANCH_PREFIX = "hustle/"
MAX_COPY_BYTES = 1_000_000

DEFAULT_EXCLUDES = (
    # installed dependencies and virtual environments
    "node_modules", ".venv", "venv", "vendor", ".bundle", "bower_components",
    # build output
    "dist", "build", "out", "target", "_build", ".next", ".nuxt", ".svelte-kit", ".turbo", ".parcel-cache",
    "*.egg-info", "*.o", "*.so", "*.dylib", "*.class", "*.pyc",
    # caches
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".cache", ".gradle", "coverage",
    ".coverage", "htmlcov",
    # loaded from the main checkout anyway
    ".claude/settings.local.json",
)


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str


def git(repo: Path | str, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def branch_name(hustle: str, increment: str) -> str:
    """`hustle/<hustle>/<increment>`; ids that are not ref-safe get a hash so two of them cannot collide."""

    def safe(part: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", part).strip("-") or "x"
        return slug if slug == part else f"{slug}-{hashlib.sha1(part.encode()).hexdigest()[:8]}"

    return f"{BRANCH_PREFIX}{safe(hustle)}/{safe(increment)}"


def excluded(rel: str, excludes: tuple[str, ...] | list[str]) -> bool:
    """A pattern with a slash matches the whole relative path, one without matches any path component."""
    parts = rel.split("/")
    for pattern in excludes:
        if "/" in pattern.strip("/"):
            if fnmatch(rel, pattern.strip("/")):
                return True
        elif any(fnmatch(p, pattern.strip("/")) for p in parts):
            return True
    return False


def ignored_files(repo: Path, excludes: tuple[str, ...] | list[str]) -> list[str]:
    """The gitignored files to copy, relative to `repo`.

    `--directory` reports a wholly ignored directory as one entry, so an excluded `node_modules` is never walked.
    """
    out = git(repo, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory", "--no-empty-directory")
    files = []
    for entry in filter(None, out.split("\0")):
        rel = entry.rstrip("/")
        if excluded(rel, excludes):
            continue
        if not entry.endswith("/"):
            files.append(rel)
            continue
        for dirpath, dirnames, filenames in os.walk(repo / rel):
            base = Path(dirpath).relative_to(repo).as_posix()
            dirnames[:] = sorted(d for d in dirnames if not excluded(f"{base}/{d}", excludes))
            files += [f"{base}/{f}" for f in sorted(filenames) if not excluded(f"{base}/{f}", excludes)]
    return [f for f in files if (repo / f).is_symlink() or (repo / f).stat().st_size <= MAX_COPY_BYTES]


def copy_ignored(repo: Path, dest: Path, excludes: tuple[str, ...] | list[str] | None = None) -> list[str]:
    copied = ignored_files(repo, DEFAULT_EXCLUDES if excludes is None else excludes)
    for rel in copied:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / rel, target, follow_symlinks=False)
    return copied


def create(
    repo: Path | str,
    root: Path | str,
    hustle: str,
    increment: str,
    *,
    excludes: list[str] | None = None,
    base: str = "main",
) -> Worktree:
    """Create the increment's worktree under `root` on a new branch off `base`, with the ignored files copied.

    `excludes` is the project's override (`Project.copy_excludes`); None means the default. An existing worktree
    for the increment is returned as it is — it holds a resumed increment's work, and copying again would
    overwrite files the session may have changed.
    """
    repo = Path(repo).resolve()
    branch = branch_name(hustle, increment)
    for wt in listed(repo):
        if wt.branch == branch:
            return wt
    path = Path(root).resolve() / branch.removeprefix(BRANCH_PREFIX)
    path.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "-b", branch, str(path), base)
    copy_ignored(repo, path, excludes)
    return Worktree(path, branch)


def listed(repo: Path | str) -> list[Worktree]:
    """The hustlebots' worktrees git knows of for `repo`; after a restart these are the orphans to resume."""
    git(repo, "worktree", "prune")
    found, path = [], None
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
        elif line.startswith("branch refs/heads/" + BRANCH_PREFIX) and path is not None:
            found.append(Worktree(path, line.removeprefix("branch refs/heads/")))
    return found


def remove(repo: Path | str, wt: Worktree) -> None:
    """Remove the worktree and its branch; `--force` because a hustlebot may leave untracked or changed files."""
    if wt.path.exists():
        git(repo, "worktree", "remove", "--force", str(wt.path))
    git(repo, "worktree", "prune")
    if git(repo, "branch", "--list", wt.branch).strip():
        git(repo, "branch", "-D", wt.branch)

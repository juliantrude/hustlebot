"""Worktree lifecycle in scratch repos: branch per increment, ignored files copied by the rules, removal."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hustlemaster import worktree
from hustlemaster.worktree import MAX_COPY_BYTES, Worktree, branch_name


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "t@example.com")
    sh(repo, "config", "user.name", "t")
    write(repo / ".gitignore", "\n".join([
        ".env", "secrets/", "node_modules/", ".venv/", "dist/", "*.log", "big.bin",
        ".claude/settings.local.json", "src/generated.json", "__pycache__/",
    ]))
    write(repo / "README.md", "tracked")
    write(repo / ".claude/settings.json", "{}")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-qm", "init")
    # the main checkout's ignored files
    write(repo / ".env", "TOKEN=1")
    write(repo / "secrets/api.key", "k")
    write(repo / "secrets/nested/deep.txt", "d")
    write(repo / "node_modules/left-pad/index.js", "module.exports = 1")
    write(repo / ".venv/bin/python", "#!")
    write(repo / "dist/app.js", "built")
    write(repo / "server.log", "log")
    write(repo / "src/generated.json", "{}")
    write(repo / "src/__pycache__/m.cpython-312.pyc", "pyc")
    write(repo / ".claude/settings.local.json", '{"env": {}}')
    (repo / "big.bin").write_bytes(b"\0" * (MAX_COPY_BYTES + 1))
    return repo


def files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}


def test_create_branches_off_main_and_copies_ignored_files_by_the_default_rules(repo, tmp_path):
    wt = worktree.create(repo, tmp_path / "wts", "h1", "A")

    assert wt.branch == "hustle/h1/A"
    assert wt.path == (tmp_path / "wts" / "h1" / "A").resolve()
    assert sh(wt.path, "rev-parse", "--abbrev-ref", "HEAD").strip() == "hustle/h1/A"
    assert sh(wt.path, "rev-parse", "HEAD") == sh(repo, "rev-parse", "main")
    assert files(wt.path) == {
        # tracked
        ".gitignore", "README.md", ".claude/settings.json",
        # ignored and copied, a wholly ignored directory included with its subdirectories
        ".env", "secrets/api.key", "secrets/nested/deep.txt", "server.log", "src/generated.json",
    }
    assert (wt.path / ".env").read_text() == "TOKEN=1"
    # the copies stay ignored: the hustlebot's one commit cannot pick them up
    assert sh(wt.path, "status", "--porcelain") == ""


def test_project_override_replaces_the_default_excludes_but_not_the_size_limit(repo, tmp_path):
    wt = worktree.create(repo, tmp_path / "wts", "h1", "A", excludes=["node_modules", "*.log", "secrets/nested"])

    copied = files(wt.path)
    assert {"dist/app.js", ".venv/bin/python", "src/__pycache__/m.cpython-312.pyc", ".claude/settings.local.json"} <= copied
    assert "secrets/api.key" in copied
    assert not any(f.startswith(("node_modules/", "secrets/nested/")) for f in copied)
    assert "server.log" not in copied
    assert "big.bin" not in copied


def test_empty_override_copies_everything_under_the_size_limit(repo, tmp_path):
    wt = worktree.create(repo, tmp_path / "wts", "h1", "A", excludes=[])
    assert "node_modules/left-pad/index.js" in files(wt.path)
    assert "big.bin" not in files(wt.path)


def test_symlinks_are_copied_as_links(repo, tmp_path):
    (repo / "secrets/current").symlink_to("api.key")
    wt = worktree.create(repo, tmp_path / "wts", "h1", "A")
    link = wt.path / "secrets/current"
    assert link.is_symlink() and link.readlink() == Path("api.key")


def test_work_in_a_worktree_does_not_touch_the_main_checkout(repo, tmp_path):
    wt = worktree.create(repo, tmp_path / "wts", "h1", "A")
    write(wt.path / "feature.txt", "new")
    write(wt.path / ".env", "TOKEN=changed")
    sh(wt.path, "add", "feature.txt")
    sh(wt.path, "commit", "-qm", "A")

    assert not (repo / "feature.txt").exists()
    assert (repo / ".env").read_text() == "TOKEN=1"
    assert sh(repo, "log", "--oneline", "main").count("\n") == 1
    assert sh(repo, "log", "--oneline", "hustle/h1/A").count("\n") == 2


def test_create_again_returns_the_existing_worktree_untouched(repo, tmp_path):
    first = worktree.create(repo, tmp_path / "wts", "h1", "A")
    write(first.path / ".env", "TOKEN=session-changed")
    write(first.path / "wip.txt", "half done")

    again = worktree.create(repo, tmp_path / "wts", "h1", "A")

    assert again == first
    assert (again.path / ".env").read_text() == "TOKEN=session-changed"
    assert (again.path / "wip.txt").exists()


def test_parallel_increments_get_separate_worktrees(repo, tmp_path):
    a = worktree.create(repo, tmp_path / "wts", "h1", "A")
    b = worktree.create(repo, tmp_path / "wts", "h1", "B")
    c = worktree.create(repo, tmp_path / "wts", "h2", "A")
    assert len({a.path, b.path, c.path}) == 3
    assert {w.branch for w in worktree.listed(repo)} == {"hustle/h1/A", "hustle/h1/B", "hustle/h2/A"}


def test_listed_finds_orphans_after_a_restart_and_ignores_other_worktrees(repo, tmp_path):
    worktree.create(repo, tmp_path / "wts", "h1", "A")
    sh(repo, "worktree", "add", "-q", "-b", "feature", str(tmp_path / "mine"))
    # a hustlemaster restart: nothing in memory, git still knows the worktree
    assert worktree.listed(repo) == [Worktree((tmp_path / "wts" / "h1" / "A").resolve(), "hustle/h1/A")]


def test_remove_deletes_worktree_and_branch_even_with_leftovers(repo, tmp_path):
    wt = worktree.create(repo, tmp_path / "wts", "h1", "A")
    write(wt.path / "untracked.txt", "left behind")
    write(wt.path / "README.md", "modified")

    worktree.remove(repo, wt)

    assert not wt.path.exists()
    assert worktree.listed(repo) == []
    assert sh(repo, "branch", "--list", wt.branch).strip() == ""
    assert (repo / ".env").exists()


def test_remove_tolerates_a_worktree_deleted_by_hand(repo, tmp_path):
    wt = worktree.create(repo, tmp_path / "wts", "h1", "A")
    subprocess.run(["rm", "-rf", str(wt.path)], check=True)
    worktree.remove(repo, wt)
    assert worktree.listed(repo) == []
    assert sh(repo, "branch", "--list", wt.branch).strip() == ""
    # the increment can start over in a fresh worktree
    assert worktree.create(repo, tmp_path / "wts", "h1", "A").path.exists()


def test_branch_names_are_ref_safe_and_do_not_collide():
    assert branch_name("h1", "parse-report_2") == "hustle/h1/parse-report_2"
    odd = [branch_name("h1", i) for i in ("a b", "a-b", "a/b", "a..b", "ä", "")]
    assert len(set(odd)) == len(odd)
    for name in odd:
        subprocess.run(["git", "check-ref-format", f"refs/heads/{name}"], check=True)


def test_create_works_with_an_odd_increment_id(repo, tmp_path):
    wt = worktree.create(repo, tmp_path / "wts", "h1", "fix the ../parser")
    assert wt.path.is_relative_to((tmp_path / "wts").resolve())
    assert sh(wt.path, "rev-parse", "--abbrev-ref", "HEAD").strip() == wt.branch

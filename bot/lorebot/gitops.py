"""Apply a proposed write operation to the repo via ``git`` subprocesses.

Flow (per spec):
  * Refuse to commit if the working tree has *unrelated* staged/modified changes.
  * If a remote is configured: ``git pull --rebase`` first, then **rebuild** the
    plan against the freshly-pulled tree (so a diff/append is computed against the
    latest content, never a stale snapshot).
  * Write the plan's files, ``git add`` them, commit with attribution
    (message includes the Discord username; author = LoreBot).
  * If a remote is configured: ``git push``; on rejection ``git pull --rebase`` and
    retry push once. If that rebase conflicts, ``git rebase --abort``, restore, and
    return both-versions info for a human.
  * No remote: commit locally and note it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .content.index import ContentIndex
from .preview import Plan, build_plan

AUTHOR = "LoreBot <lorebot@sundered-isles.local>"


class GitError(RuntimeError):
    pass


@dataclass
class ApplyResult:
    ok: bool
    message: str
    committed: bool = False
    pushed: bool = False
    no_remote: bool = False
    conflict: bool = False
    commit_sha: str | None = None
    both_versions: dict[str, str] = field(default_factory=dict)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True
    )
    if check and proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


def has_remote(repo: Path) -> bool:
    return bool(_git(repo, "remote").stdout.strip())


def _rel_paths(repo: Path, plan: Plan) -> list[str]:
    root = Path(repo).resolve()
    return [str(Path(p).resolve().relative_to(root)) for p in plan.files]


def _unrelated_dirty(repo: Path, allowed: set[str]) -> str | None:
    out = _git(repo, "status", "--porcelain").stdout
    dirty = []
    for line in out.splitlines():
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and path not in allowed:
            dirty.append(path)
    return ", ".join(sorted(dirty)) if dirty else None


def _write(plan: Plan) -> None:
    for abspath, content in plan.files.items():
        p = Path(abspath)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _capture_both_versions(repo: Path, rels: list[str]) -> dict[str, str]:
    """During an in-progress rebase, capture ours (:2) vs theirs (:3)."""
    both: dict[str, str] = {}
    for rel in rels:
        ours = _git(repo, "show", f":2:{rel}", check=False).stdout
        theirs = _git(repo, "show", f":3:{rel}", check=False).stdout
        both[rel] = f"<<<<<<< ours\n{ours}\n=======\n{theirs}\n>>>>>>> theirs"
    return both


def push_with_retry(repo: Path, rels: list[str]) -> ApplyResult:
    """Push HEAD; on rejection, ``pull --rebase`` and retry once. On a rebase
    conflict, abort and return both versions. Assumes HEAD is already committed."""
    repo = Path(repo)
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    if _git(repo, "push", check=False).returncode == 0:
        return ApplyResult(ok=True, message="Committed and pushed.", committed=True,
                           pushed=True, commit_sha=sha)

    pull = _git(repo, "pull", "--rebase", check=False)
    if pull.returncode != 0:
        both = _capture_both_versions(repo, rels)
        _git(repo, "rebase", "--abort", check=False)
        return ApplyResult(
            ok=False,
            message="Push failed and the rebase hit a conflict on the same file — "
            "a human needs to reconcile. Both versions captured below.",
            committed=True,
            conflict=True,
            commit_sha=_git(repo, "rev-parse", "HEAD").stdout.strip(),
            both_versions=both,
        )

    if _git(repo, "push", check=False).returncode == 0:
        return ApplyResult(ok=True, message="Committed and pushed (after rebase).",
                           committed=True, pushed=True,
                           commit_sha=_git(repo, "rev-parse", "HEAD").stdout.strip())
    return ApplyResult(ok=False, message="Committed locally but the push kept failing.",
                       committed=True, commit_sha=sha)


def _commit_message(plan: Plan, username: str) -> str:
    """Single op keeps the terse one-line form; a batch summarises with a body
    listing one line per op."""
    if not plan.is_batch:
        op = plan.ops[0]
        return f"lore: {op.verb} {op.target} (via @{username})"
    subject = f"lore: {len(plan.ops)} changes via batch (via @{username})"
    body = "\n".join(f"- {op.label}" for op in plan.ops)
    return f"{subject}\n\n{body}"


def apply_operations(repo_path, content_root, operations, username: str) -> ApplyResult:
    """Apply one op (dict) or a batch (list of dicts) as a single commit."""
    repo = Path(repo_path)
    content_root = Path(content_root)

    # Build once pre-pull to learn the target paths and surface blocking errors
    # (slug collision / unknown field) before touching git.
    plan = build_plan(content_root, ContentIndex(content_root), operations)
    rels = _rel_paths(repo, plan)
    allowed = set(rels)

    dirty = _unrelated_dirty(repo, allowed)
    if dirty is not None:
        return ApplyResult(
            ok=False,
            message=f"The working tree has unrelated changes I won't touch: {dirty}. "
            "Commit or stash them first.",
        )

    remote = has_remote(repo)
    if remote:
        pull = _git(repo, "pull", "--rebase", check=False)
        if pull.returncode != 0:
            _git(repo, "rebase", "--abort", check=False)
            return ApplyResult(
                ok=False,
                message="Could not rebase onto the remote before committing: "
                + (pull.stderr.strip() or pull.stdout.strip()),
                conflict=True,
            )
        # Rebuild against the freshly-pulled tree so diffs/appends aren't stale.
        plan = build_plan(content_root, ContentIndex(content_root), operations)
        rels = _rel_paths(repo, plan)

    _write(plan)
    _git(repo, "add", *rels)
    message = _commit_message(plan, username)
    _git(repo, "commit", "-m", message, "--author", AUTHOR)
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    if not remote:
        return ApplyResult(ok=True, message="Committed locally — no remote configured.",
                           committed=True, no_remote=True, commit_sha=sha)

    return push_with_retry(repo, rels)

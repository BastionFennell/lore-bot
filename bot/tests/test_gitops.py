from __future__ import annotations

import subprocess
from pathlib import Path

from lorebot import gitops
from tests.conftest import git


def make_bare(tmp_path: Path) -> Path:
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)],
        check=True, capture_output=True, text=True,
    )
    return bare


def add_origin(repo: Path, bare: Path) -> None:
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-u", "origin", "main")


def clone(bare: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", str(bare), str(dest)], check=True, capture_output=True, text=True)
    git(dest, "config", "user.email", "c2@example.com")
    git(dest, "config", "user.name", "Clone2")
    return dest


CREATE_OP = {
    "tool": "create_entry",
    "input": {
        "type": "location", "title": "Gull Reef", "tags": ["reef"],
        "summary": "A treacherous reef.",
        "body_sections": [{"heading": "Description", "content": "Sharp coral."}],
    },
}
UPDATE_OP = {
    "tool": "update_field",
    "input": {"slug": "captain-powderkeg", "field": "status", "value": "dead"},
}
BATCH_OPS = [
    {"tool": "add_glossary_term",
     "input": {"term": "Kin", "definition": "Bound crew.", "link_slug": None}},
    {"tool": "add_glossary_term",
     "input": {"term": "Fathoms", "definition": "A depth measure.", "link_slug": None}},
    {"tool": "add_timeline_event",
     "input": {"date_in_fiction": "0849-02-11", "description": "A battle at sea",
               "related_slugs": None}},
]


def test_batch_applies_in_one_commit(content_repo, content_root):
    res = gitops.apply_operations(content_repo, content_root, BATCH_OPS, "you")
    assert res.ok and res.committed
    # exactly one new commit on top of init
    count = git(content_repo, "rev-list", "--count", "HEAD").stdout.strip()
    assert count == "2"
    # all three changes present in that one commit
    glossary = (content_root / "glossary" / "glossary.yaml").read_text()
    assert "id: kin" in glossary and "id: fathoms" in glossary
    events = (content_root / "timeline" / "events.yaml").read_text()
    assert "A battle at sea" in events
    # commit message: batch subject + per-op body lines
    subject = git(content_repo, "log", "-1", "--format=%s").stdout.strip()
    assert subject == "lore: 3 changes via batch (via @you)"
    body = git(content_repo, "log", "-1", "--format=%b").stdout
    assert "- glossary: kin" in body
    assert "- glossary: fathoms" in body
    assert "- timeline:" in body


def test_per_item_apply_produces_independent_commits(content_repo, content_root):
    # Each op confirmed on its own => one commit per op, terse per-op message.
    for op in BATCH_OPS:
        res = gitops.apply_operations(content_repo, content_root, [op], "you")
        assert res.ok and res.committed
    count = git(content_repo, "rev-list", "--count", "HEAD").stdout.strip()
    assert count == "4"  # init + 3 independent commits
    # each commit is a normal single-op message, not a batch subject
    subjects = git(content_repo, "log", "--format=%s", "-3").stdout.splitlines()
    assert all("changes via batch" not in s for s in subjects)
    assert any("add kin" in s for s in subjects)
    assert any("add fathoms" in s for s in subjects)
    # all three changes landed
    glossary = (content_root / "glossary" / "glossary.yaml").read_text()
    assert "id: kin" in glossary and "id: fathoms" in glossary
    assert "A battle at sea" in (content_root / "timeline" / "events.yaml").read_text()


def test_apply_item_two_after_item_one_rebuilds_from_disk(content_repo, content_root):
    # Two appends to the same section, applied one at a time. Item 2's plan must
    # be rebuilt against the on-disk tree (which now contains item 1), so both
    # lines survive rather than item 2 clobbering item 1.
    op1 = {"tool": "append_to_entry",
           "input": {"slug": "captain-powderkeg", "section_heading": "Recent History",
                     "content": "First independent line."}}
    op2 = {"tool": "append_to_entry",
           "input": {"slug": "captain-powderkeg", "section_heading": "Recent History",
                     "content": "Second independent line."}}
    assert gitops.apply_operations(content_repo, content_root, [op1], "you").ok
    assert gitops.apply_operations(content_repo, content_root, [op2], "you").ok
    text = (content_root / "lore" / "npcs" / "captain-powderkeg.md").read_text()
    assert "First independent line." in text
    assert "Second independent line." in text
    count = git(content_repo, "rev-list", "--count", "HEAD").stdout.strip()
    assert count == "3"  # init + 2 commits


def test_no_remote_local_commit(content_repo, content_root):
    res = gitops.apply_operations(content_repo, content_root, CREATE_OP, "you")
    assert res.ok and res.committed and res.no_remote
    assert (content_root / "lore" / "locations" / "gull-reef.md").exists()
    log = git(content_repo, "log", "--oneline").stdout
    assert "create gull-reef" in log
    # attribution: author is LoreBot
    author = git(content_repo, "log", "-1", "--format=%an <%ae>").stdout.strip()
    assert author == "LoreBot <lorebot@sundered-isles.local>"


def test_commit_push_happy(content_repo, content_root, tmp_path):
    bare = make_bare(tmp_path)
    add_origin(content_repo, bare)
    res = gitops.apply_operations(content_repo, content_root, UPDATE_OP, "captainuser")
    assert res.ok and res.pushed
    show = subprocess.run(
        ["git", "-C", str(bare), "show", "HEAD:content/lore/npcs/captain-powderkeg.md"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "status: dead" in show
    msg = subprocess.run(
        ["git", "-C", str(bare), "log", "-1", "--format=%s"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "update captain-powderkeg (via @captainuser)" in msg


def test_divergent_rebase_retry_succeeds(content_repo, content_root, tmp_path):
    bare = make_bare(tmp_path)
    add_origin(content_repo, bare)

    # local commit editing file A (glossary)
    glossary = content_root / "glossary" / "glossary.yaml"
    glossary.write_text(glossary.read_text() + "\n# local note\n")
    git(content_repo, "commit", "-am", "local glossary note")

    # clone2 edits a DIFFERENT file B (timeline) and pushes -> remote diverges
    c2 = clone(bare, tmp_path / "c2")
    events = c2 / "content" / "timeline" / "events.yaml"
    events.write_text(events.read_text() + "\n# remote note\n")
    git(c2, "commit", "-am", "remote timeline note")
    git(c2, "push")

    res = gitops.push_with_retry(content_repo, ["content/glossary/glossary.yaml"])
    assert res.ok and res.pushed and not res.conflict
    # both changes survive on the remote
    g = subprocess.run(["git", "-C", str(bare), "show", "HEAD:content/glossary/glossary.yaml"],
                       check=True, capture_output=True, text=True).stdout
    t = subprocess.run(["git", "-C", str(bare), "show", "HEAD:content/timeline/events.yaml"],
                       check=True, capture_output=True, text=True).stdout
    assert "# local note" in g
    assert "# remote note" in t


def test_true_conflict_returns_both_versions(content_repo, content_root, tmp_path):
    bare = make_bare(tmp_path)
    add_origin(content_repo, bare)
    target = content_root / "lore" / "npcs" / "captain-powderkeg.md"

    # local rewrites the whole file
    target.write_text("LOCAL VERSION\n")
    git(content_repo, "commit", "-am", "local rewrite")

    # clone2 rewrites the SAME file differently and pushes -> conflicting
    c2 = clone(bare, tmp_path / "c2")
    (c2 / "content" / "lore" / "npcs" / "captain-powderkeg.md").write_text("REMOTE VERSION\n")
    git(c2, "commit", "-am", "remote rewrite")
    git(c2, "push")

    res = gitops.push_with_retry(content_repo, ["content/lore/npcs/captain-powderkeg.md"])
    assert res.conflict is True
    assert not res.ok
    assert res.both_versions  # ours/theirs captured
    blob = next(iter(res.both_versions.values()))
    assert "LOCAL VERSION" in blob and "REMOTE VERSION" in blob
    # rebase was aborted — no rebase in progress, our commit is intact
    assert not (content_repo / ".git" / "rebase-merge").exists()
    assert not (content_repo / ".git" / "rebase-apply").exists()
    assert target.read_text() == "LOCAL VERSION\n"


def test_bails_on_unrelated_dirty_tree(content_repo, content_root):
    # dirty an unrelated file (append to its body, keeping frontmatter valid)
    sundering = content_root / "lore" / "concepts" / "the-sundering.md"
    sundering.write_text(sundering.read_text() + "\n<!-- hand edit -->\n")
    res = gitops.apply_operations(content_repo, content_root, CREATE_OP, "you")
    assert not res.ok
    assert "unrelated changes" in res.message


def test_concurrent_applies_serialize(content_repo, content_root, tmp_path):
    """Rapid ✅s run apply_operations from multiple threads at once; the repo
    lock must serialize them so both commits land instead of git racing."""
    from concurrent.futures import ThreadPoolExecutor

    bare = make_bare(tmp_path)
    add_origin(content_repo, bare)

    def op(term):
        return {"tool": "add_glossary_term",
                "input": {"term": term, "definition": f"About {term}."}}

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(gitops.apply_operations, content_repo, content_root, [op("Alpha")], "tester"),
            pool.submit(gitops.apply_operations, content_repo, content_root, [op("Beta")], "tester"),
        ]
        results = [f.result() for f in futures]

    assert all(r.ok for r in results), [r.message for r in results]
    log = git(content_repo, "log", "--oneline").stdout
    assert "alpha" in log.lower() or "Alpha" in log
    assert "beta" in log.lower() or "Beta" in log
    blob = (content_root / "glossary" / "glossary.yaml").read_text()
    assert "Alpha" in blob and "Beta" in blob

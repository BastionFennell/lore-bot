"""Turn proposed write operations into a preview/diff + warnings, and into a
concrete set of files to write (a :class:`Plan`) that gitops can apply.

A *proposed write* is a JSON-serialisable ``{"tool": ..., "input": ...}`` dict —
exactly what the engine captures from the LLM and what pending.py persists. A
proposal is a **list** of one or more such ops (the model may batch several
additions in a single turn, e.g. "add these five glossary terms"). The plan is
(re)built from those dicts against the *current* content tree, so applying after
a ``git pull`` re-reads the latest state.

Per-op preview rules (per spec):
  * create_entry        -> full rendered markdown of the would-be file
  * append/update       -> unified diff of the affected section/field
  * glossary/timeline   -> the new/changed item rendered
Warnings appended: unknown ``{{slug}}`` refs, new section-heading creation.
Blocking (raises instead of previewing): slug collisions, unknown fields/slugs.
A blocking error in ANY op blocks the whole batch, naming the offending op.

Batching detail: ops are planned **in order**, threading each op's result into
the next via an in-memory overlay, so several ops touching the same file (five
glossary terms all landing in ``glossary.yaml``) accumulate instead of
clobbering one another.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

from .content import entries, glossary, timeline
from .content.index import ContentIndex

CONFIRM_FOOTER = "React ✅ to commit or ❌ to cancel."

# Short kind labels used in batch headers and commit-message bodies.
KIND_LABEL = {
    "create_entry": "create",
    "append_to_entry": "append",
    "update_field": "update",
    "add_glossary_term": "glossary",
    "add_timeline_event": "timeline",
}


@dataclass
class OpPlan:
    """A single planned operation."""
    kind: str
    verb: str  # commit-message verb: create | update | append | add
    target: str  # slug / term id / event id
    files: dict[str, str]  # absolute path -> full new content
    title_line: str  # bold header, e.g. "**Add glossary term `kin`**"
    block: str  # the fenced code/diff block for this op
    warnings: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        """Short "kind: target" label for batch headers / commit bodies."""
        return f"{KIND_LABEL.get(self.kind, self.kind)}: {self.target}"


@dataclass
class Plan:
    """A whole proposal — one or more ops rendered into a single preview."""
    ops: list[OpPlan]
    files: dict[str, str]
    preview: str
    warnings: list[str] = field(default_factory=list)

    # --- single-op convenience (keeps the common case ergonomic) -----------
    @property
    def is_batch(self) -> bool:
        return len(self.ops) > 1

    @property
    def kind(self) -> str:
        return self.ops[0].kind

    @property
    def verb(self) -> str:
        return self.ops[0].verb

    @property
    def target(self) -> str:
        return self.ops[0].target


def _diff(old: str, new: str, label: str) -> str:
    lines = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        fromfile=f"{label} (before)",
        tofile=f"{label} (after)",
        lineterm="",
    )
    return "\n".join(lines)


def _op_ref(op: dict) -> str:
    """Best-effort short label for an op, usable before it is fully planned
    (for naming the offending op when a blocking error is raised)."""
    tool = op.get("tool", "?")
    data = op.get("input", {}) or {}
    target = (
        data.get("slug")
        or data.get("term")
        or data.get("title")
        or data.get("date_in_fiction")
        or "?"
    )
    return f"{KIND_LABEL.get(tool, tool)}: {target}"


def build_plan(content_root: Path, index: ContentIndex, operations) -> Plan:
    """Build a :class:`Plan` from a single ``{"tool", "input"}`` dict or a list
    of them. May raise ``entries.SlugCollisionError`` / ``entries.EntryError``
    (both block); on a batch the error names the offending op."""
    content_root = Path(content_root)
    ops = [operations] if isinstance(operations, dict) else list(operations)
    if not ops:
        raise entries.EntryError("No operation to preview.")
    n = len(ops)

    overlay: dict[str, str] = {}  # abspath -> pending new content
    created_slugs: set[str] = set()
    op_plans: list[OpPlan] = []
    for i, op in enumerate(ops, start=1):
        try:
            op_plan = _plan_one(content_root, index, op, overlay, created_slugs)
        except (entries.SlugCollisionError, entries.EntryError) as e:
            if n == 1:
                raise
            raise type(e)(
                f"Batch blocked at op {i}/{n} ({_op_ref(op)}): {e}"
            ) from e
        overlay.update(op_plan.files)  # next op sees this op's result
        if op_plan.kind == "create_entry":
            created_slugs.add(op_plan.target)
        op_plans.append(op_plan)

    return _compose(op_plans)


def build_plans(content_root: Path, index: ContentIndex, operations) -> list[Plan]:
    """Build one single-op :class:`Plan` **per op**, threading a cumulative
    overlay so op *k*'s preview reflects ops 1..k-1 (previews stack in the same
    file just as the commits will). Used by the per-op confirmation UX, where
    each op gets its own preview message and its own ✅/❌.

    For a batch (N > 1) each plan's ``preview`` is headed ``k/N — kind: target``;
    a single op (N == 1) is identical to :func:`build_plan` (no header). May
    raise ``entries.SlugCollisionError`` / ``entries.EntryError`` (both block);
    on a batch the error names the offending op.
    """
    content_root = Path(content_root)
    ops = [operations] if isinstance(operations, dict) else list(operations)
    if not ops:
        raise entries.EntryError("No operation to preview.")
    n = len(ops)

    overlay: dict[str, str] = {}
    created_slugs: set[str] = set()
    plans: list[Plan] = []
    for i, op in enumerate(ops, start=1):
        try:
            op_plan = _plan_one(content_root, index, op, overlay, created_slugs)
        except (entries.SlugCollisionError, entries.EntryError) as e:
            if n == 1:
                raise
            raise type(e)(
                f"Batch blocked at op {i}/{n} ({_op_ref(op)}): {e}"
            ) from e
        overlay.update(op_plan.files)
        if op_plan.kind == "create_entry":
            created_slugs.add(op_plan.target)
        plans.append(_compose([op_plan], index=i, total=n))
    return plans


def _compose(op_plans: list[OpPlan], *, index: int | None = None, total: int | None = None) -> Plan:
    files: dict[str, str] = {}
    warnings: list[str] = []
    for op in op_plans:
        files.update(op.files)
        warnings.extend(op.warnings)

    n = len(op_plans)
    if index is not None and total and total > 1:
        # A single op rendered as one item of a larger batch: k/N header.
        op = op_plans[0]
        preview = f"**{index}/{total} — {op.label}**\n\n{op.block}\n\n" + _footer(warnings)
    elif n == 1:
        op = op_plans[0]
        preview = op.title_line + "\n\n" + op.block + "\n\n" + _footer(warnings)
    else:
        sections = []
        for i, op in enumerate(op_plans, start=1):
            sections.append(f"**{i}/{n} — {op.label}**\n\n{op.block}")
        preview = "\n\n".join(sections) + "\n\n" + _footer(warnings)

    return Plan(ops=op_plans, files=files, preview=preview, warnings=warnings)


def _plan_one(
    content_root: Path,
    index: ContentIndex,
    operation: dict,
    overlay: dict[str, str],
    created_slugs: set[str],
) -> OpPlan:
    tool = operation["tool"]
    data = operation.get("input", {}) or {}

    if tool == "create_entry":
        return _plan_create(content_root, index, data, created_slugs)
    if tool == "append_to_entry":
        return _plan_append(index, data, overlay)
    if tool == "update_field":
        return _plan_update(index, data, overlay)
    if tool == "add_glossary_term":
        return _plan_glossary(content_root, index, data, overlay)
    if tool == "add_timeline_event":
        return _plan_timeline(content_root, index, data, overlay)
    raise entries.EntryError(f"Not a write operation: {tool!r}")


def _sections_to_dict(body_sections) -> dict[str, str]:
    """Accept either the strict array-of-pairs form or a plain dict."""
    if isinstance(body_sections, dict):
        return body_sections
    out: dict[str, str] = {}
    for pair in body_sections or []:
        out[pair["heading"]] = pair["content"]
    return out


def _plan_create(
    content_root: Path, index: ContentIndex, data: dict, created_slugs: set[str]
) -> OpPlan:
    sections = _sections_to_dict(data.get("body_sections"))
    created = entries.create_entry(
        content_root,
        index,
        entry_type=data["type"],
        title=data["title"],
        tags=data.get("tags") or [],
        summary=data.get("summary", ""),
        body_sections=sections,
        extra_slugs=created_slugs,
    )
    warnings = _slug_warnings(index, created.content, ignore={created.slug})
    return OpPlan(
        kind="create_entry",
        verb="create",
        target=created.slug,
        files={str(created.path): created.content},
        title_line=f"**New {data['type']} — `{created.slug}`**",
        block=f"```markdown\n{created.content}```",
        warnings=warnings,
    )


def _plan_append(index: ContentIndex, data: dict, overlay: dict[str, str]) -> OpPlan:
    entry = index.lookup(data["slug"])
    current = overlay.get(str(entry.path)) if entry else None
    res = entries.append_to_entry(
        index,
        slug=data["slug"],
        section_heading=data["section_heading"],
        content=data["content"],
        current_content=current,
    )
    warnings = _slug_warnings(index, data["content"])
    if res.created_heading:
        warnings.append(f'New section heading "## {res.heading}" will be created.')
    diff = _diff(res.old_section, res.new_section, f"{data['slug']} · ## {res.heading}")
    return OpPlan(
        kind="append_to_entry",
        verb="append to",
        target=data["slug"],
        files={str(res.path): res.new_content},
        title_line=f"**Append to `{data['slug']}` under `## {res.heading}`**",
        block=f"```diff\n{diff}\n```",
        warnings=warnings,
    )


def _plan_update(index: ContentIndex, data: dict, overlay: dict[str, str]) -> OpPlan:
    entry = index.lookup(data["slug"])
    current = overlay.get(str(entry.path)) if entry else None
    res = entries.update_field(
        index, slug=data["slug"], field=data["field"], value=data["value"],
        current_content=current,
    )
    diff = _diff(
        f"{res.field}: {res.old_value}",
        f"{res.field}: {res.new_value}",
        f"{data['slug']} · {res.field}",
    )
    return OpPlan(
        kind="update_field",
        verb="update",
        target=data["slug"],
        files={str(res.path): res.new_content},
        title_line=f"**Update `{data['slug']}` — `{res.field}`**",
        block=f"```diff\n{diff}\n```",
        warnings=[],
    )


def _plan_glossary(
    content_root: Path, index: ContentIndex, data: dict, overlay: dict[str, str]
) -> OpPlan:
    gpath = str(Path(content_root) / glossary.GLOSSARY_RELPATH)
    res = glossary.add_glossary_term(
        content_root,
        term=data["term"],
        definition=data["definition"],
        link_slug=data.get("link_slug"),
        current_content=overlay.get(gpath),
    )
    warnings = []
    link = data.get("link_slug")
    if link and link not in index:
        warnings.append(f"link_slug '{link}' does not resolve to a known entry.")
    verb_word = "Update" if res.is_update else "Add"
    return OpPlan(
        kind="add_glossary_term",
        verb="update" if res.is_update else "add",
        target=res.item["id"],
        files={str(res.path): res.new_content},
        title_line=f"**{verb_word} glossary term `{res.item['id']}`**",
        block=f"```yaml\n{res.rendered_item}```",
        warnings=warnings,
    )


def _plan_timeline(
    content_root: Path, index: ContentIndex, data: dict, overlay: dict[str, str]
) -> OpPlan:
    tpath = str(Path(content_root) / timeline.TIMELINE_RELPATH)
    res = timeline.add_timeline_event(
        content_root,
        date_in_fiction=data["date_in_fiction"],
        description=data["description"],
        related_slugs=data.get("related_slugs"),
        current_content=overlay.get(tpath),
    )
    warnings = []
    for slug in data.get("related_slugs") or []:
        if slug not in index:
            warnings.append(f"related slug '{slug}' does not resolve to a known entry.")
    return OpPlan(
        kind="add_timeline_event",
        verb="add",
        target=res.item["id"],
        files={str(res.path): res.new_content},
        title_line=f"**Add timeline event `{res.item['id']}`**",
        block=f"```yaml\n{res.rendered_item}```",
        warnings=warnings,
    )


def _slug_warnings(index: ContentIndex, text: str, *, ignore: set[str] | None = None) -> list[str]:
    unknown = entries.unknown_slug_refs(index, text, ignore=ignore)
    return [f"Unknown reference `{{{{{s}}}}}` (renders as a stub link)." for s in unknown]


def _footer(warnings: list[str]) -> str:
    parts = []
    if warnings:
        parts.append("⚠️ " + "\n⚠️ ".join(warnings))
    parts.append(CONFIRM_FOOTER)
    return "\n\n".join(parts)

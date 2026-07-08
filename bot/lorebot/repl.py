"""Terminal REPL: ``python -m lorebot.repl``.

Drives the same engine as the Discord bot with y/n confirmation, no Discord
needed — only ANTHROPIC_API_KEY + REPO_PATH. Handy for developing prompts and
exercising the write/commit path locally.
"""

from __future__ import annotations

import logging

from . import engine as engine_mod
from . import gitops, llm, preview, siteurls
from .config import ConfigError, load_config
from .content import entries as entries_mod
from .content.index import ContentIndex


def _prompt(text: str) -> str:
    try:
        return input(text)
    except EOFError:
        return ""


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    try:
        config = load_config(require_discord=False)
    except ConfigError as e:
        raise SystemExit(str(e))

    client = llm.build_client(config.anthropic_api_key)
    recent: list[dict] = []
    pending_ctx: dict | None = None
    pending_kind: str | None = None  # "clarify" | "confirm"

    print("LoreBot REPL — type a request (Ctrl-D or 'quit' to exit).")
    while True:
        line = _prompt("\n> ").strip()
        if line in {"quit", "exit"}:
            break
        if not line:
            continue

        index = ContentIndex(config.content_root)
        ctx = engine_mod.EngineContext(
            message_text=line,
            author="you",
            recent_messages=list(recent),
            history_fetch=None,
            pending=pending_ctx,
            correction=line if pending_kind else None,
        )
        outcome = engine_mod.run_engine(
            client=client,
            model=config.anthropic_model,
            context=ctx,
            index=index,
            effort=config.anthropic_effort,
        )
        recent.append({"author": "you", "content": line})
        recent = recent[-5:]

        if isinstance(outcome, engine_mod.ProposedWrite):
            try:
                plans = preview.build_plans(config.content_root, index, outcome.operations)
            except (entries_mod.SlugCollisionError, entries_mod.EntryError) as e:
                print(f"⚠️  {e}")
                pending_ctx = pending_kind = None
                continue
            n = len(plans)
            # Each op is confirmed and committed on its own (a batch is N prompts,
            # N independent commits) — yes to some, no to others.
            for k, plan in enumerate(plans, start=1):
                print("\n" + plan.preview)
                label = "Apply?" if n == 1 else f"Apply {k}/{n}?"
                answer = _prompt(f"\n{label} [y/N] ").strip().lower()
                if answer == "y":
                    result = gitops.apply_operations(
                        config.repo_path, config.content_root, [outcome.operations[k - 1]], "you"
                    )
                    print(("✅ " if result.ok else "⚠️  ") + result.message)
                    for u in siteurls.page_urls(
                        config.site_base_url, config.content_root, [outcome.operations[k - 1]]
                    ):
                        print(f"📖 {u}")
                    if result.commit_sha and not config.site_base_url:
                        print(f"Commit {result.commit_sha[:8]}")
                else:
                    print("Skipped — nothing committed.")
            pending_ctx = pending_kind = None
        elif isinstance(outcome, engine_mod.Clarification):
            print(outcome.question)
            if outcome.options:
                for o in outcome.options:
                    print(f"  • {o}")
            pending_ctx = {"question": outcome.question}
            pending_kind = "clarify"
        elif isinstance(outcome, engine_mod.Conversational):
            print(outcome.text)
            pending_ctx = pending_kind = None
        else:  # Error
            print(f"⚠️  {outcome.message}")
            pending_ctx = pending_kind = None


if __name__ == "__main__":
    main()

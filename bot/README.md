# LoreBot — Phase 2

LoreBot is the Discord authoring bot for the **Sundered Isles Chronicle**. It
listens in exactly one channel (`#captains-log`), turns natural-language requests
from the two players into structured content operations, **previews every write
as a diff**, and commits to git only after a `✅` reaction. Everything it does, a
human can do by editing the markdown in [`../content`](../content) directly — the
bot is a convenience layer with zero lock-in.

See [`../lorebot-spec.md`](../lorebot-spec.md) for the full design. This is
**Phase 2** (writes + the machinery). `/ask` Q&A runs through the same loop but
gets its quality pass in Phase 3.

## What's here

```
bot/
  pyproject.toml         # uv/pip project (Python 3.11)
  .env.example           # copy to .env and fill in
  lorebot/
    config.py            # env loading + validation, allow-lists
    main.py              # discord client entrypoint (python -m lorebot.main)
    repl.py              # terminal REPL (python -m lorebot.repl) — same engine, no Discord
    engine.py            # the LLM agentic loop (transport-agnostic)
    llm.py               # anthropic wrapper + tool JSON schemas
    fuzzy.py             # rapidfuzz entity pre-matching -> hints
    pending.py           # SQLite pending-operation state machine
    preview.py           # previews/diffs + warnings; builds the write "plan"
    gitops.py            # pull/rebase/commit/push via git subprocess
    discord_io.py        # 2000-char message splitting, reaction constants
    content/
      slugify.py         # identical rules to site/src/lib/urls.mjs
      index.py           # slug index over /content (port of lore-index.mjs)
      entries.py         # create / append / update markdown entries
      glossary.py        # add/update glossary.yaml terms (with id)
      timeline.py        # append events.yaml events (with id)
  tests/                 # pytest; fixtures copy /content into tmp git repos
```

## Setup

### 1. Python + dependencies

Python 3.11 is pinned via `.tool-versions` (asdf) and `bot/.python-version`.
This project uses [`uv`](https://docs.astral.sh/uv/):

```bash
cd bot
uv sync                 # create .venv and install deps (incl. dev)
uv run pytest           # run the test suite
```

If you'd rather not use `uv`:

```bash
cd bot
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'   # or install the deps listed in pyproject.toml
pytest
```

### 2. Discord app

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
   → **New Application**.
2. **Bot** tab → **Add Bot**. Copy the **token** into `DISCORD_TOKEN`.
3. Under **Privileged Gateway Intents**, enable **MESSAGE CONTENT INTENT**
   (the bot needs to read message text).
4. **Invite URL / permissions.** The bot needs: *Read Messages/View Channel*,
   *Send Messages*, *Add Reactions*, *Read Message History*. That permissions
   integer is **68608**. Build an invite URL with the `bot` scope:

   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot&permissions=68608
   ```

   Open it, pick your server, authorize. Then restrict the bot to the one
   `#captains-log` channel via that channel's permissions if you like — the bot
   also enforces the allow-list itself and ignores every other channel.
5. Enable **Developer Mode** (User Settings → Advanced) so you can right-click →
   **Copy ID** for the server (`GUILD_ID`), the channel (`CHANNEL_ID`), and each
   player (`ALLOWED_USER_IDS`, comma-separated).

### 3. Environment

```bash
cp .env.example .env
# fill in DISCORD_TOKEN, ANTHROPIC_API_KEY, GUILD_ID, CHANNEL_ID, ALLOWED_USER_IDS
```

`REPO_PATH` defaults to this repo's root (the parent of `bot/`); set it only if
the bot runs against a checkout elsewhere.

## Running the bot

```bash
uv run python -m lorebot.main
```

Then in `#captains-log`, from an allow-listed account:

> add a glossary term: Iron Vow — a sworn oath that binds a character's fate

The bot replies with a preview; react `✅` to commit (pull → write → commit →
push) or `❌` to cancel. A correction reply ("put it under Recent History
instead") re-runs the parse and shows a revised preview. Pending operations
expire after 30 minutes.

## REPL mode (no Discord)

The REPL drives the same engine with `y/n` confirmation — handy for iterating on
prompts and exercising the commit path. It needs only `ANTHROPIC_API_KEY` and
`REPO_PATH`:

```bash
uv run python -m lorebot.repl
> update Powderkeg's page to say she lost the naval battle
```

## Tests

```bash
uv run pytest          # no network, no real API calls
```

Every content/git test runs against a **throwaway copy** of `/content` inside a
temporary git repo, so the real repo is never touched. Engine tests use a
scripted fake Anthropic client. Slug parity is verified against the real
`urls.mjs` by shelling out to Node (skipped if Node is absent).

## Notes for Phase 3

- `/ask` already works through the engine: a question classifies as read-tools +
  a conversational answer (no write tool fires). The quality pass — ranked
  retrieval, cited answers, snippets — is Phase 3.
- `search_lore` in `engine.py` is a **basic** keyword/term-frequency scan over
  titles/summaries/body with a naive snippet. It's the obvious upgrade target.
- Timeline events via the bot exist (`add_timeline_event`) but timeline UX/quality
  is also a Phase 3 concern.

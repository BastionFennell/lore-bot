# LoreBot — Phase 2

LoreBot is the Discord authoring bot for the **Sundered Isles Chronicle**. It
listens in exactly one channel (`#captains-log`), turns natural-language requests
from the two players into structured content operations, **previews every write
as a diff**, and commits to git only after a `✅` reaction. Everything it does, a
human can do by editing the markdown in [`../content`](../content) directly — the
bot is a convenience layer with zero lock-in.

See [`../lorebot-spec.md`](../lorebot-spec.md) for the full design. Writes + the
machinery landed in Phase 2; **Phase 3** adds the `/ask` quality pass — ranked
corpus search and cited answers (see [Phase 3](#phase-3--ask-quality-pass-done)
below).

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

## Phase 3 — `/ask` quality pass (done)

- **Ranked search** (`lorebot/search.py`, replacing the old placeholder in
  `engine.py`). It searches the whole corpus — lore/character/map entries
  (title, tags, summary, body), glossary terms, and timeline events — and tags
  each result `entry | glossary | timeline`. Ranking is field-weighted
  (title/term ≫ tags ≫ summary/definition ≫ body); multi-term queries reward
  documents that cover more *distinct* terms over ones that just repeat one.
  `rapidfuzz` adds typo tolerance ("tidebund" → **Tidebound**), with fuzzy hits
  always scoring below exact ones. Results are capped at 8 and rendered as
  readable, citable lines (each carries its entry slug / glossary id); snippets
  are cut at sentence boundaries (word boundaries as a fallback), ~200 chars,
  never mid-word.
- **Cited answers.** For `/ask`, the model cites sources inline with `{{slug}}`
  / `{{glossary-id}}` refs. Both transports (`main.py`, `repl.py`) run
  Conversational answers through `lorebot/refrender.py`, which turns each ref
  into `**Title** (<url>)` — entry refs → entry page URL, glossary ids →
  glossary anchor (entry wins on a name collision). Unknown refs render as the
  bare name (no dead link); with no `SITE_BASE_URL` set they render as plain
  `**Title**` / term name. URL construction is shared with `siteurls.py` so
  post-commit links and inline citations stay in one place. Previews/diffs keep
  raw `{{refs}}` — that's the committed content.
- Timeline events via the bot (`add_timeline_event`) are now also surfaced in
  search results.

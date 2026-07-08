# Sundered Isles Chronicle — Project Spec

A two-person play-by-post RP companion: GitHub is the source of truth, a static site is the reading surface, and an LLM-powered Discord bot ("LoreBot") lets a non-technical player create and edit lore content through natural language in a single dedicated channel.

## Overview & Goals

- **Players:** 2 (one technical, one not). No auth, no moderation tooling, no multi-tenancy.
- **Game:** Sundered Isles (built on Ironsworn: Starforged). Play-by-post happens in Discord RP channels, which are **out of scope** — the bot never reads them.
- **Problem:** Discord is bad at organized, navigable, presentable reference material: lore books, glossaries, NPC pages, maps, timelines.
- **Solution:**
  1. A **git repo** of markdown/YAML lore content.
  2. A **static site** built from the repo: lore book, glossary, NPC/character pages, maps, timeline.
  3. **LoreBot**, listening in one channel (`#captains-log`), which translates natural-language requests into structured content operations, previews them, and commits to GitHub on confirmation. It can also answer questions about existing lore.

### Design principles

1. **GitHub is the single source of truth.** Discord is an authoring interface. The archive must survive Discord.
2. **The bot is a convenience layer with zero lock-in.** Everything the bot can do, a human can do by editing markdown files directly. If the bot dies, the project still works.
3. **Structured intents, not freeform file editing.** The LLM fills templates and calls narrow tools; it never has arbitrary write access to the repo.
4. **Preview before commit.** Every write operation shows a diff/preview in Discord and requires explicit confirmation.
5. **Everything is revertible.** Every bot commit is attributed and atomic. `git log` is the moderation system.
6. **Bounded context.** The bot reads a small default window of `#captains-log` and escalates via explicit, capped tools — never unbounded history access, never other channels.

---

## Architecture

```
┌──────────────────┐  lore intents (LLM,     ┌──────────────┐
│    Discord        │  preview → ✅ commit)   │              │
│  #captains-log    │ ──────────────────────► │  Git repo    │──► Static site build
│     LoreBot       │ ◄────────────────────── │  (GitHub)    │    (GitHub Pages /
└──────────────────┘  reads for /ask &        └──────────────┘     Netlify / etc.)
                      edit-context
```

- **LoreBot:** long-running process (small VPS, Fly.io, Railway, or a Raspberry Pi). discord.py or discord.js — builder's choice; spec examples assume Python/discord.py.
- **LLM:** Anthropic API (Claude), used for intent parsing, content extraction, and lore Q&A.
- **Repo:** single GitHub repo containing all content + the site generator.
- **Site:** static site generator (Astro recommended; Eleventy or Hugo acceptable). Rebuild triggered by push (GitHub Action).
- **Local persistence:** SQLite for bot state (pending operations). Fully reconstructible; losing it loses nothing but an in-flight preview.

---

## Repo Structure

```
/content
  /lore
    /locations/*.md        # e.g. kells-hollow.md
    /factions/*.md
    /npcs/*.md
    /concepts/*.md         # rules-of-the-world, cosmology, etc.
  /glossary
    glossary.yaml          # term, definition, optional link to a lore slug
  /characters              # PC pages (hand-authored, bot-editable)
    *.md
  /maps
    /images/*.png|jpg
    *.md                   # one page per map: frontmatter + optional pin annotations
  /timeline
    events.yaml            # manual + bot-added in-fiction events
/site                      # static site generator project
/bot                       # LoreBot source
/.github/workflows         # build & deploy on push
```

### Slugs

- Every lore entry, character, and map has a stable, unique, kebab-case slug derived from its title at creation (`house-veldrane`). Slugs never change after creation (renames update `title` frontmatter only).
- Uniqueness is enforced across all of `/lore`, `/characters`, `/maps` (one namespace), so `{{slug}}` links are unambiguous.

### Cross-linking syntax

- `{{slug}}` anywhere in any markdown body renders as a link to that entry's page.
- The site build generates **backlinks**: each page lists "Referenced by" — every other page that references its slug.
- The bot's preview step warns on `{{unknown-slug}}` references (typo guard) but does not block them (forward references to not-yet-written entries are allowed and render as "stub" links).

---

## Frontmatter Schemas

All content types share a base schema; the LLM fills these templates.

```yaml
# Base (all types)
title: House Veldrane
slug: house-veldrane
type: faction            # location | faction | npc | concept | character | map
tags: [nobility, naval]
created: 2026-07-08
updated: 2026-07-08
summary: One-sentence description shown in search results and hover cards.
```

Type-specific additions:

```yaml
# npc
status: alive            # alive | dead | missing | unknown
affiliation: house-veldrane   # slug reference
first_appearance: "The Mutiny at Kell's Hollow"   # optional free text

# location
region: the-shattered-reach   # slug reference
map: shattered-reach-map      # optional slug of a map entry

# faction
leader: captain-powderkeg     # slug reference
disposition: hostile          # ally | neutral | hostile | unknown

# character (PC)
player: discord-user-id
```

Body sections use conventional `##` headings the bot can target by name (e.g., `## Description`, `## Recent History`, `## Relationships`). `append_to_entry` targets these headings; if the named section doesn't exist, the bot proposes creating it in the preview.

---

## LoreBot: Scope & Channel

- The bot operates in **exactly one channel: `#captains-log`**. Every message there from an allowed user is assumed to be bot input (an edit intent, a question, an answer to a pending question, or a lore dump in progress).
- The bot has **no read access to RP channels or any other channel**. Allow-list: one server ID, one channel ID, two user IDs. Everything else ignored.
- The bot replies inline in the channel — no threads. Clarifications and previews are ordinary back-and-forth messages.

## LoreBot: LLM Context & Tools

### Default context per invocation

- The invoking message **plus the last 5 messages in `#captains-log`** (both users' — it's a shared log; seeing the partner's recent messages helps with "add what we just discussed").
- The full index of existing content: slugs + titles + types + summaries.
- The pending-operation state, if any (see Conversation Model).

### Read tools (LLM-invocable, no confirmation needed)

```
fetch_channel_history(limit, before_message_id?)
  → Returns up to `limit` additional messages from #captains-log, newest-first
    from the given point. HARD CAP: 50 messages per invocation, #captains-log only.
  → Use cases: multi-message lore dumps ("add all of that"), "as we discussed
    earlier", reconstructing a longer conversation.

query_lore(slug) 
  → Returns the full current content (frontmatter + body) of one entry.
  → MUST be called before proposing an append/update to an existing entry, so
    the proposed content fits what's already there (no redundant or
    contradictory additions, correct section targeting).

search_lore(query)
  → Keyword/fuzzy search over titles, tags, summaries, and body text.
  → Returns matching slugs + summaries + snippet. Used for /ask and for
    resolving vague references before asking the user.
```

### Write tools (all go through preview → confirmation)

```
create_entry(type, title, tags, summary, body_sections: {heading: content})
  → Creates a new lore/character/map markdown file from the type's template.
  → Bot generates the slug, checks uniqueness, previews full rendered entry.

append_to_entry(slug, section_heading, content)
  → Appends content under an existing (or new, with confirmation) ## heading.

update_field(slug, field, value)
  → Frontmatter changes only: status, disposition, affiliation, tags, summary, etc.
  → Validates field against the type's schema.

add_glossary_term(term, definition, link_slug?)
  → Adds/updates a term in glossary.yaml.

add_timeline_event(date_in_fiction, description, related_slugs?)
  → Appends to timeline/events.yaml.
```

### Control tools

```
request_clarification(question, options?)
  → Bot asks the user; the operation becomes "pending" (see Conversation Model).

no_action(reason)
  → Message wasn't an actionable edit (e.g., chit-chat, or it was an answer
    consumed by a pending operation); bot responds conversationally.
```

**Ambiguity rule:** before invoking the LLM, the bot fuzzy-matches candidate entity names in the message against slugs/titles and passes the results as hints. The LLM may use `search_lore` to investigate further. If a target still can't be resolved confidently, the LLM MUST call `request_clarification` rather than guess. Example: "Update the captain's page" → "Which captain — **Ferocious** or **Powderkeg**?"

## Confirmation Flow

1. User writes a natural-language edit in `#captains-log`.
2. Bot (via LLM, using read tools as needed) parses intent → write tool call(s).
3. Bot replies with a **preview**: the rendered new entry, or a before/after diff of the changed section, plus any warnings (unknown `{{slugs}}`, new section headings).
4. User reacts ✅ to commit, ❌ to cancel, or replies with a correction ("no, put it under Relationships instead") which re-runs the parse with the correction appended.
5. On ✅: bot pulls latest, applies the change, commits with attribution, pushes. Replies with a link to the page (site URL) once the build completes (or immediately with the commit link).
6. On pull/push conflict (rare at 2 users): bot pulls, rebases its change, retries once; if the same file section was manually edited, it aborts and shows both versions for a human decision.

## Conversation Model (Pending State + Windowed History)

- **No threads.** All interaction is plain messages in `#captains-log`.
- **Pending-operation state machine** (SQLite): when the bot asks a clarifying question or shows a preview, it records `(user, operation, question)` as pending. That user's **next message in the channel** is treated as the answer/correction.
- **One pending operation per user at a time.** If a new unrelated intent arrives while one is pending, bot replies: "Still waiting on: *[question]* — answer it, or ❌ to cancel first."
- Pending operations expire after 30 minutes (bot says so and cancels).
- **Multi-message lore dumps:** user writes several messages, then a final message like "add all that as a new location, Kell's Hollow." The default 5-message window usually covers it; if the dump is longer or the LLM detects it started earlier, it calls `fetch_channel_history` to capture the rest. The preview shows exactly what was captured, so scope errors are caught before commit.

## `/ask` Retrieval

- "What do we know about the Kell's Hollow mutiny?" → LLM uses `search_lore` + `query_lore` to gather relevant entries, answers with links to the pages cited.
- `/ask` interactions never invoke write tools. (Same LLM loop; the intent classifies as a question, so only read tools fire.)
- No vector DB at this scale; `search_lore` over the index + full-text is sufficient. Upgrade path exists if content grows large.

---

## Static Site

**Recommended:** Astro (content collections map cleanly to the content types). Requirements regardless of generator:

- **Lore book:** browsable by type and tag; each page renders frontmatter fields appropriately (status badges for NPCs, disposition for factions), body, and auto-generated "Referenced by" backlinks.
- **Glossary:** single page from `glossary.yaml`, alphabetized, anchors per term; terms link to lore pages where `link_slug` is set.
- **Character pages:** PC pages with portrait support (image path in frontmatter).
- **Maps:** pan/zoom image viewer (Leaflet with `L.imageOverlay` / CRS.Simple), optional pins from the map entry's annotations linking to lore slugs. v1 can be a plain responsive image; viewer is a fast follow.
- **Timeline:** vertical timeline page from `timeline/events.yaml`, sorted by in-fiction date, events linking to related lore entries.
- **Search:** client-side (Pagefind or Fuse.js over a build-time index).
- **`{{slug}}` link resolution and backlink generation** happen at build time.
- Deploy: GitHub Pages or Netlify, rebuilt on push via GitHub Action.

---

## Security & Ops Notes

- Bot allow-lists: one server ID, one channel ID (`#captains-log`), two user IDs. Everything else ignored — the bot requests only the permissions/intents needed to read that channel.
- GitHub access via a fine-grained PAT or GitHub App scoped to this one repo.
- LLM calls only on `#captains-log` messages from allowed users. RP prose never touches the API.
- `fetch_channel_history` is server-side capped (50) regardless of what the LLM requests, and can only target `#captains-log`.
- All bot commits attributed: committer = bot, message includes Discord username, e.g. `lore: update house-veldrane (via @username)`.
- SQLite holds only pending-operation state; losing it loses nothing durable.

---

## Build Phases

**Phase 1 — Content + site skeleton.** Repo structure, frontmatter schemas, static site rendering hand-authored lore/glossary/character/map/timeline content, `{{slug}}` links + backlinks, deploy pipeline. *No bot yet — the site is fully usable with manual git editing.*

**Phase 2 — LoreBot writes.** Discord bot scaffold, LLM loop with read/write/control tools, confirmation flow, pending-state machine, fuzzy pre-matching. Glossary + lore creation/updates via natural language.

**Phase 3 — LoreBot reads.** `/ask` Q&A with citations, `search_lore` quality pass, timeline events via bot.

**Phase 4 — Polish.** Map viewer with pins, hover cards on `{{slug}}` links, client-side search, nicer preview rendering (embeds), build-status feedback in Discord.

**Future / out of scope for now:** RP channel mirroring into a scenes archive. The repo structure should not preclude adding `/content/scenes` later, but nothing in V1 depends on it.

## Acceptance Tests (Phase 2–3 focus)

1. "Add a glossary term: 'Iron Vow — a sworn oath that binds a character's fate'" → preview → ✅ → term appears on site.
2. "Update the captain's page to say she lost the naval battle" with two captains existing → bot asks which; "Powderkeg" → correct preview → ✅ → correct file updated, other untouched.
3. Before that preview in (2), the bot called `query_lore("captain-powderkeg")` and the proposed append does not duplicate content already in the section.
4. Seven-message lore dump + "add all that as a new location, Kell's Hollow" → LLM calls `fetch_channel_history` to capture beyond the 5-message window → preview contains all seven messages' content, nothing else.
5. User replies with a correction at preview stage ("put it under Recent History") → revised preview → ✅.
6. New intent while another is pending → bot refuses and restates the pending question.
7. Manual git edit to a file, then a bot edit to the same file's different section → both survive.
8. ❌ at preview → nothing committed; repo unchanged.
9. "What do we know about House Veldrane?" → answer cites and links the entry; no write tools invoked, no preview shown.
10. A message posted in any channel other than `#captains-log` → bot does nothing, no LLM call is made.

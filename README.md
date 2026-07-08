# Sundered Isles Chronicle

A living archive for a two-person play-by-post RP campaign set in the Sundered
Isles (Ironsworn: Starforged). **GitHub is the single source of truth**: all lore
is plain markdown/YAML in [`/content`](./content), and the [`/site`](./site)
Astro project is just the reading surface that renders it.

This repository is **Phase 1** of [`lorebot-spec.md`](./lorebot-spec.md) — content
schemas, the static site, sample content, and the deploy pipeline. The Discord
authoring bot is Phase 2 (see [`/bot/README.md`](./bot/README.md)).

## Repository layout

```
.tool-versions             # pins Node 20.9.0 (via asdf)
content/                   # source of truth — edit these files by hand
  lore/{locations,factions,npcs,concepts}/*.md
  glossary/glossary.yaml
  characters/*.md
  maps/*.md + maps/images/*.png
  timeline/events.yaml
site/                      # Astro 5 static site (renders content/)
bot/                       # Phase 2 placeholder
.github/workflows/deploy.yml
```

## Running the site

Node 20 is required. This repo ships a `.tool-versions` file, so if you use
[asdf](https://asdf-vm.com/) it selects Node 20.9.0 automatically:

```bash
cd site
npm install
npm run dev      # local dev server
npm run build    # static build into site/dist/
```

If your shell defaults to an older Node, either `asdf install` / `asdf reshim`,
or prefix commands with `ASDF_NODEJS_VERSION=20.9.0 asdf exec …`.

## Adding content by hand

Every lore entry, character, and map is a markdown file with YAML frontmatter.
Copy an existing file in the matching `content/` folder and edit it.

### Slugs and entry IDs

- Each entry has a stable, unique, kebab-case `slug`. **The `slug` is the entry
  ID** — the site config forces this via each collection loader's `generateId`,
  so the filename is conventional but the frontmatter `slug` is authoritative.
  (We chose `generateId` over filename-validation; keep filename == slug anyway
  for tidiness.)
- Slugs are **unique across `content/lore`, `content/characters`, and
  `content/maps`** (one namespace). A collision fails the build with a clear
  error naming both files — see `site/src/lib/lore-index.mjs`.

### Frontmatter schemas

Base fields (all types): `title`, `slug`, `type`, `tags`, `created`, `updated`,
`summary`. Dates are ISO (`2026-07-08`). Type-specific fields:

| type      | extra fields |
|-----------|--------------|
| location  | `region` (slug), `map` (slug, optional) |
| faction   | `leader` (slug), `disposition`: `ally`\|`neutral`\|`hostile`\|`unknown` |
| npc       | `status`: `alive`\|`dead`\|`missing`\|`unknown`, `affiliation` (slug), `first_appearance` (text) |
| concept   | — |
| character | `player` (Discord id string), `portrait` (image path, optional) |
| map       | `image` (path relative to `content/maps/`, e.g. `images/foo.png`) |

Body sections use conventional `##` headings the Phase 2 bot will target by name:
`## Description`, `## Recent History`, `## Relationships`.

### Cross-links, stubs, and backlinks

- Write `{{some-slug}}` anywhere in a body to link to that entry. It renders as a
  link using the target's title (`site/src/plugins/remark-lore-links.mjs`).
- A `{{slug}}` with no matching entry renders as a visible **stub**
  (`<span class="stub-link">`) and does **not** fail the build — forward
  references to not-yet-written entries are allowed.
- Every lore/character/map page auto-generates a **Referenced by** section listing
  entries that link to it (`site/src/lib/backlinks.ts`).
- Frontmatter slug references (`affiliation`, `leader`, `region`, `map`,
  glossary `link_slug`, timeline `related`) also render as links.

### Glossary and timeline

- `content/glossary/glossary.yaml`: array of `{ id, term, definition, link_slug? }`.
  The `id` is required by Astro's file loader; `link_slug` links the term to a
  lore entry. Rendered alphabetized with a per-term anchor (`id` = slugified term).
- `content/timeline/events.yaml`: array of
  `{ id, date, display_date?, description, related? }`. **`date` is a sortable
  in-fiction date string** (e.g. `0847-03-12`); events are ordered by it
  ascending. `display_date` is the human-readable label (e.g.
  "12th of Highstorm, 847").

## Deploying (GitHub Pages)

`.github/workflows/deploy.yml` builds `./site` with `withastro/action` on Node 20
and deploys to GitHub Pages on every push to `main`. It injects `SITE_URL` and
`BASE_PATH` from the repo context (`https://<owner>.github.io` and `/<repo>/`) so
project-pages paths work; `astro.config.mjs` reads those env vars (defaults:
`https://example.github.io` and `/`).

**One-time setup:** in the repo's **Settings → Pages**, set **Source** to
**GitHub Actions**.

## For Phase 2 (the bot)

The build-time slug index lives at `site/src/lib/lore-index.mjs` and exports a
`slug → { url, title, type }` map (plus `lookupSlug`). The bot can reuse it
instead of re-parsing frontmatter. See `bot/README.md`.

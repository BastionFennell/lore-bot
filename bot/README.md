# LoreBot — Phase 2

Placeholder. The Discord bot is not part of Phase 1.

See [`../lorebot-spec.md`](../lorebot-spec.md) for the full design:

- Listens in exactly one channel (`#captains-log`).
- Translates natural-language requests into structured content operations.
- Previews every write as a diff and commits to GitHub only on `✅` confirmation.
- Answers lore questions via `/ask` using read-only tools.

Phase 2 will add the bot source here (discord.py or discord.js). It reuses the
build-time slug index that the site already computes — see
`../site/src/lib/lore-index.mjs`, which returns a `slug → { url, title, type }`
map by scanning `../content` frontmatter.

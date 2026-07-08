import { defineCollection, z } from 'astro:content';
import { glob, file } from 'astro/loaders';

// Entry ID strategy: we force the collection entry `id` to be the frontmatter
// `slug` via each glob loader's `generateId`. Filenames are conventional but the
// slug is authoritative, so {{slug}} links and backlinks key off `id === slug`.

const baseFields = {
  title: z.string(),
  slug: z.string(),
  tags: z.array(z.string()).default([]),
  created: z.coerce.date(),
  updated: z.coerce.date(),
  summary: z.string(),
};

const generateId = ({ data }: { data: Record<string, unknown> }) =>
  String(data.slug);

// --- lore: locations | factions | npcs | concepts (one collection) ---------
const lore = defineCollection({
  loader: glob({
    base: '../content/lore',
    pattern: '**/*.md',
    generateId,
  }),
  schema: z.discriminatedUnion('type', [
    z.object({
      ...baseFields,
      type: z.literal('location'),
      region: z.string().optional(), // slug reference
      map: z.string().optional(), // slug reference to a map entry
    }),
    z.object({
      ...baseFields,
      type: z.literal('faction'),
      leader: z.string().optional(), // slug reference
      disposition: z.enum(['ally', 'neutral', 'hostile', 'unknown']),
    }),
    z.object({
      ...baseFields,
      type: z.literal('npc'),
      status: z.enum(['alive', 'dead', 'missing', 'unknown']),
      affiliation: z.string().optional(), // slug reference
      first_appearance: z.string().optional(),
    }),
    z.object({
      ...baseFields,
      type: z.literal('concept'),
    }),
  ]),
});

// --- characters (PCs) -------------------------------------------------------
const characters = defineCollection({
  loader: glob({
    base: '../content/characters',
    pattern: '**/*.md',
    generateId,
  }),
  schema: z.object({
    ...baseFields,
    type: z.literal('character'),
    player: z.string(),
    portrait: z.string().optional(), // optional image path
  }),
});

// --- maps -------------------------------------------------------------------
const maps = defineCollection({
  loader: glob({
    base: '../content/maps',
    pattern: '*.md',
    generateId,
  }),
  // image() processes the co-located file (images/foo.png, relative to the entry)
  // into a hashed, base-aware asset so the map still has a single source of truth.
  schema: ({ image }) =>
    z.object({
      ...baseFields,
      type: z.literal('map'),
      image: image(),
    }),
});

// --- glossary (YAML array) --------------------------------------------------
const glossary = defineCollection({
  loader: file('../content/glossary/glossary.yaml'),
  schema: z.object({
    term: z.string(),
    definition: z.string(),
    link_slug: z.string().optional(),
  }),
});

// --- timeline (YAML array) --------------------------------------------------
const timeline = defineCollection({
  loader: file('../content/timeline/events.yaml'),
  schema: z.object({
    date: z.string(), // sortable in-fiction date, e.g. 0847-03-12
    display_date: z.string().optional(),
    description: z.string(),
    related: z.array(z.string()).default([]), // slug references
  }),
});

export const collections = { lore, characters, maps, glossary, timeline };

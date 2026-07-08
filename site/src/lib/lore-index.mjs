// Build-time slug index: slug -> { url, title, type }.
//
// Scans the frontmatter of every linkable entry under ../content (lore +
// characters + maps — the single slug namespace per the spec) and asserts slug
// uniqueness across all of them, failing the build loudly on a collision.
//
// The remark {{slug}} plugin imports this so it can resolve references while
// compiling markdown (before Astro's content collections are available).
//
// Phase 2 note: the LoreBot can import this same module to get the authoritative
// slug -> {url, title, type} map without re-implementing frontmatter parsing.

import { readdirSync, statSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import matter from 'gray-matter';
import { urlForType } from './urls.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const CONTENT_ROOT = resolve(__dirname, '../../../content');

function walkMarkdown(dir) {
  const out = [];
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of entries) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      out.push(...walkMarkdown(full));
    } else if (name.endsWith('.md')) {
      out.push(full);
    }
  }
  return out;
}

function collectFiles() {
  // maps: only top-level *.md (skip /images); lore: recursive; characters: flat.
  const loreFiles = walkMarkdown(join(CONTENT_ROOT, 'lore'));
  const characterFiles = walkMarkdown(join(CONTENT_ROOT, 'characters'));
  const mapDir = join(CONTENT_ROOT, 'maps');
  let mapFiles = [];
  try {
    mapFiles = readdirSync(mapDir)
      .filter((n) => n.endsWith('.md'))
      .map((n) => join(mapDir, n));
  } catch {
    /* no maps yet */
  }
  return [...loreFiles, ...characterFiles, ...mapFiles];
}

function buildIndex() {
  const index = new Map();
  const seen = new Map(); // slug -> first file that claimed it

  for (const file of collectFiles()) {
    const { data } = matter(readFileSync(file, 'utf8'));
    const { slug, title, type } = data;
    if (!slug) {
      throw new Error(`[lore-index] Missing "slug" in frontmatter: ${file}`);
    }
    if (seen.has(slug)) {
      throw new Error(
        `[lore-index] Duplicate slug "${slug}" found in:\n` +
          `  - ${seen.get(slug)}\n` +
          `  - ${file}\n` +
          `Slugs must be unique across /lore, /characters and /maps.`
      );
    }
    seen.set(slug, file);
    index.set(slug, {
      slug,
      title: title || slug,
      type,
      url: urlForType(type, slug),
    });
  }
  return index;
}

// Built once at module load — a duplicate slug throws here and fails the build.
export const loreIndex = buildIndex();

export function lookupSlug(slug) {
  return loreIndex.get(slug) || null;
}

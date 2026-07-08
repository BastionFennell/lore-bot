// Backlinks helper: for a given slug, which entries reference it via {{slug}}?
//
// Scans the raw body of every lore/character/map entry once (cached), building a
// reverse index target-slug -> referrers. Pages render this as "Referenced by".

import { getCollection } from 'astro:content';
import { urlForType } from './urls.mjs';

export interface Referrer {
  slug: string;
  title: string;
  url: string;
}

const REF_RE = /\{\{\s*([a-z0-9][a-z0-9-]*)\s*\}\}/g;

let cache: Map<string, Referrer[]> | null = null;

async function buildBacklinks(): Promise<Map<string, Referrer[]>> {
  if (cache) return cache;

  const [lore, characters, maps] = await Promise.all([
    getCollection('lore'),
    getCollection('characters'),
    getCollection('maps'),
  ]);
  const all = [...lore, ...characters, ...maps];

  const map = new Map<string, Referrer[]>();
  for (const entry of all) {
    const referrerSlug = entry.id; // id === frontmatter slug (see content.config.ts)
    const referrer: Referrer = {
      slug: referrerSlug,
      title: entry.data.title,
      url: urlForType(entry.data.type, referrerSlug),
    };
    const body = entry.body ?? '';
    const seen = new Set<string>();
    let m: RegExpExecArray | null;
    REF_RE.lastIndex = 0;
    while ((m = REF_RE.exec(body)) !== null) {
      const target = m[1];
      if (target === referrerSlug || seen.has(target)) continue;
      seen.add(target);
      if (!map.has(target)) map.set(target, []);
      map.get(target)!.push(referrer);
    }
  }
  cache = map;
  return map;
}

export async function backlinksFor(slug: string): Promise<Referrer[]> {
  const map = await buildBacklinks();
  return [...(map.get(slug) ?? [])].sort((a, b) => a.title.localeCompare(b.title));
}

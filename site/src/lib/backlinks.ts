// Backlinks helper: for a given slug, which entries reference it via {{slug}}?
//
// Scans the raw body of every lore/character/map entry once (cached), building a
// reverse index target-slug -> referrers. Pages render this as "Referenced by".
// Glossary definitions and timeline descriptions also CONTRIBUTE referrers (a
// {{slug}} in a definition/event points at the entry), labelled distinctly.
// Glossary terms and timeline events have no pages, so they never RECEIVE a
// backlink section — only entry pages call backlinksFor().

import { getCollection } from 'astro:content';
import { basePath, urlForType } from './urls.mjs';

export interface Referrer {
  slug: string;
  title: string;
  url: string;
}

const REF_RE = /\{\{\s*([a-z0-9][a-z0-9-]*)\s*\}\}/g;

// Scan one string for {{ref}}s and register `referrer` against each distinct
// target (skipping self-references and duplicates within this string).
function collectRefs(
  map: Map<string, Referrer[]>,
  text: string,
  referrer: Referrer,
  selfKey: string
): void {
  const seen = new Set<string>();
  let m: RegExpExecArray | null;
  REF_RE.lastIndex = 0;
  while ((m = REF_RE.exec(text)) !== null) {
    const target = m[1];
    if (target === selfKey || seen.has(target)) continue;
    seen.add(target);
    if (!map.has(target)) map.set(target, []);
    map.get(target)!.push(referrer);
  }
}

let cache: Map<string, Referrer[]> | null = null;

async function buildBacklinks(): Promise<Map<string, Referrer[]>> {
  if (cache) return cache;

  const [lore, characters, maps, glossary, timeline] = await Promise.all([
    getCollection('lore'),
    getCollection('characters'),
    getCollection('maps'),
    getCollection('glossary'),
    getCollection('timeline'),
  ]);

  const map = new Map<string, Referrer[]>();

  // Entry bodies (lore + characters + maps) — the primary source of backlinks.
  for (const entry of [...lore, ...characters, ...maps]) {
    const referrerSlug = entry.id; // id === frontmatter slug (see content.config.ts)
    const referrer: Referrer = {
      slug: referrerSlug,
      title: entry.data.title,
      url: urlForType(entry.data.type, referrerSlug),
    };
    collectRefs(map, entry.body ?? '', referrer, referrerSlug);
  }

  // Glossary definitions — contribute, labelled "Glossary: <Term>".
  for (const term of glossary) {
    const referrer: Referrer = {
      slug: `glossary:${term.id}`,
      title: `Glossary: ${term.data.term}`,
      url: `${basePath()}glossary/#${term.id}`,
    };
    collectRefs(map, term.data.definition ?? '', referrer, term.id);
  }

  // Timeline descriptions — contribute, labelled "Timeline: <date>".
  for (const event of timeline) {
    const referrer: Referrer = {
      slug: `timeline:${event.id}`,
      title: `Timeline: ${event.data.display_date ?? event.data.date}`,
      url: `${basePath()}timeline/`,
    };
    collectRefs(map, event.data.description ?? '', referrer, event.id);
  }

  cache = map;
  return map;
}

export async function backlinksFor(slug: string): Promise<Referrer[]> {
  const map = await buildBacklinks();
  return [...(map.get(slug) ?? [])].sort((a, b) => a.title.localeCompare(b.title));
}

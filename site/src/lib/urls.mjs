// URL helpers shared by the remark plugin, the slug index, and page code.
// Kept as plain .mjs so it can be imported from both remark (Node) and Astro.

export function basePath() {
  let b = process.env.BASE_PATH || '/';
  if (!b.startsWith('/')) b = '/' + b;
  if (!b.endsWith('/')) b += '/';
  return b;
}

// Maps a content type to its URL section. The four lore types all live under /lore.
export function sectionForType(type) {
  if (type === 'character') return 'characters';
  if (type === 'map') return 'maps';
  return 'lore'; // location | faction | npc | concept
}

export function urlForType(type, slug) {
  return `${basePath()}${sectionForType(type)}/${slug}/`;
}

export function tagUrl(tag) {
  return `${basePath()}tags/${slugify(tag)}/`;
}

export function withBase(path) {
  const b = basePath();
  return `${b}${path.replace(/^\/+/, '')}`;
}

export function slugify(s) {
  return String(s)
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

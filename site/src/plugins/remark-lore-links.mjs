// Remark plugin: replace {{slug}} in markdown text with a link to the entry.
//
// - Known slug     -> <a class="lore-link" href="…">Entry Title</a>
// - Known glossary -> <a class="lore-link" href="…/glossary/#id">Term</a>
//   (entry slug WINS on a name collision with a glossary id)
// - Unknown ref    -> <span class="stub-link" title="unknown entry">{{ref}}</span>
//   (forward references never fail the build — see spec).
//
// Runs at markdown compile time and resolves against the build-time slug index.

import { loreIndex, lookupGlossary } from '../lib/lore-index.mjs';

const LINK_RE = /\{\{\s*([a-z0-9][a-z0-9-]*)\s*\}\}/g;

function expand(value) {
  const nodes = [];
  let last = 0;
  let m;
  LINK_RE.lastIndex = 0;
  while ((m = LINK_RE.exec(value)) !== null) {
    const [full, slug] = m;
    if (m.index > last) {
      nodes.push({ type: 'text', value: value.slice(last, m.index) });
    }
    const entry = loreIndex.get(slug); // entry slug takes precedence
    const term = entry ? null : lookupGlossary(slug);
    if (entry) {
      nodes.push({
        type: 'link',
        url: entry.url,
        title: entry.title,
        data: { hProperties: { className: ['lore-link'] } },
        children: [{ type: 'text', value: entry.title }],
      });
    } else if (term) {
      nodes.push({
        type: 'link',
        url: term.url,
        title: term.name,
        data: { hProperties: { className: ['lore-link'] } },
        children: [{ type: 'text', value: term.name }],
      });
    } else {
      // Unknown / not-yet-written ref -> visible stub, no build failure.
      nodes.push({
        type: 'html',
        value: `<span class="stub-link" title="unknown entry">{{${slug}}}</span>`,
      });
    }
    last = m.index + full.length;
  }
  if (last < value.length) {
    nodes.push({ type: 'text', value: value.slice(last) });
  }
  return nodes;
}

function transform(node) {
  if (!node || !Array.isArray(node.children)) return;
  const rebuilt = [];
  for (const child of node.children) {
    if (child.type === 'text' && child.value.includes('{{')) {
      rebuilt.push(...expand(child.value));
    } else {
      transform(child);
      rebuilt.push(child);
    }
  }
  node.children = rebuilt;
}

export default function remarkLoreLinks() {
  return (tree) => {
    transform(tree);
  };
}

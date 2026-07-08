// Remark plugin: replace {{slug}} in markdown text with a link to the entry.
//
// - Known slug  -> <a class="lore-link" href="…">Entry Title</a>
// - Unknown slug -> <span class="stub-link" title="unknown entry">{{slug}}</span>
//   (forward references never fail the build — see spec).
//
// Runs at markdown compile time and resolves against the build-time slug index.

import { loreIndex } from '../lib/lore-index.mjs';

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
    const entry = loreIndex.get(slug);
    if (entry) {
      nodes.push({
        type: 'link',
        url: entry.url,
        title: entry.title,
        data: { hProperties: { className: ['lore-link'] } },
        children: [{ type: 'text', value: entry.title }],
      });
    } else {
      // Unknown / not-yet-written slug -> visible stub, no build failure.
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

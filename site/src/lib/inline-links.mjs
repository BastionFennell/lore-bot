// Resolve {{ref}} cross-links inside PLAIN-TEXT strings (glossary definitions,
// timeline event descriptions) to HTML, at build time.
//
// These strings live in YAML, not markdown, so they never pass through the
// remark pipeline. This mirrors that pipeline's resolution rules:
//   - known entry slug   -> <a class="lore-link" href="…">Entry Title</a>
//   - known glossary id  -> <a class="lore-link" href="…/glossary/#id">Term</a>
//     (entry slug WINS on a name collision — see lookup order below)
//   - unknown ref        -> <span class="stub-link" …>{{ref}}</span>
//
// The input is plain text, NOT trusted HTML: everything is HTML-escaped first,
// then only our own generated anchors are injected. Consume via `set:html`.

import { lookupSlug, lookupGlossary } from './lore-index.mjs';

const REF_RE = /\{\{\s*([a-z0-9][a-z0-9-]*)\s*\}\}/g;

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function inlineLinks(text) {
  // Escape first so definitions cannot smuggle HTML; {{ }} survive escaping.
  const escaped = escapeHtml(text ?? '');
  return escaped.replace(REF_RE, (_full, ref) => {
    const entry = lookupSlug(ref); // entry slug takes precedence
    if (entry) {
      return `<a class="lore-link" href="${escapeHtml(entry.url)}">${escapeHtml(entry.title)}</a>`;
    }
    const term = lookupGlossary(ref);
    if (term) {
      return `<a class="lore-link" href="${escapeHtml(term.url)}">${escapeHtml(term.name)}</a>`;
    }
    // Unknown / not-yet-written ref -> visible stub, no build failure.
    return `<span class="stub-link" title="unknown entry">{{${escapeHtml(ref)}}}</span>`;
  });
}

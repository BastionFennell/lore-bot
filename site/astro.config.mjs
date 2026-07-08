// @ts-check
import { defineConfig } from 'astro/config';
import remarkLoreLinks from './src/plugins/remark-lore-links.mjs';

// `site` and `base` come from env so the GitHub Action can inject the real
// project-pages values (https://<owner>.github.io and /<repo>) at build time.
const SITE_URL = process.env.SITE_URL || 'https://example.github.io';
const BASE_PATH = process.env.BASE_PATH || '/';

export default defineConfig({
  site: SITE_URL,
  base: BASE_PATH,
  trailingSlash: 'always',
  markdown: {
    remarkPlugins: [remarkLoreLinks],
  },
});

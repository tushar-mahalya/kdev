# kdev website

The project site, live at **https://kdev-cli.pages.dev**: a static
[Astro](https://astro.build) build on Cloudflare Pages (project `kdev-cli`).

```bash
npm ci
npm run dev          # http://localhost:4321
npm run build        # dist/: pages, Pagefind search index, _headers with CSP
npm run check        # astro check (types)
npm run check:links  # every internal link and anchor in dist/ resolves
npm run preview      # wrangler pages dev on the built dist/ (real 404s and headers)
```

- Docs live in `src/content/docs/*.md` (adapted from the root README).
- `/changelog` renders the repository's own `CHANGELOG.md`; nothing to copy.
- No third-party requests: fonts are self-hosted via Fontsource, search is
  Pagefind running in the browser. `scripts/postbuild.mjs` hashes the inline
  scripts into the Content-Security-Policy, so keep new scripts as module
  `<script>` tags in components.
- Every animation stops under `prefers-reduced-motion`, and anything that loops
  has a pause control or stops when off screen.

## Deploying

`.github/workflows/website.yml` builds and checks every PR, deploys a preview
(`pr-<n>.kdev-cli.pages.dev`) for same-repo PRs, and deploys `main` to production. It needs:

| where | name | value |
|---|---|---|
| repo secret | `CLOUDFLARE_API_TOKEN` | API token with *Cloudflare Pages: Edit* |
| repo secret | `CLOUDFLARE_ACCOUNT_ID` | your Cloudflare account ID |
| repo variable | `SITE_URL` | `https://kdev-cli.pages.dev` (or a custom domain later) |

A custom domain is attached to the Pages project in the Cloudflare dashboard.
Manual deploy: `npx wrangler login && SITE_URL=https://kdev-cli.pages.dev npm run deploy`.

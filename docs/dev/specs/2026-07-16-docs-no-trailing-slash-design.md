# Docs URLs without trailing slashes

## Goal

Make the CubePlex docs site use one canonical URL shape: every non-root URL
has no trailing slash, while requests that still include a trailing slash
receive a permanent redirect to the slashless URL.

## Context

The Docusaurus site currently leaves `trailingSlash` unset. The generated
routes, sitemap, and links therefore follow Docusaurus' default static output
shape rather than an explicit project-wide URL policy. Cloudflare Pages also
needs an edge rule for old slash-suffixed URLs, otherwise both forms can remain
reachable.

The CubePi docs site already uses a Pages advanced-mode `_worker.js` copied
from `static/` into the deployment output. CubePlex can use the same deployment
boundary for URL normalization.

## Approaches considered

1. **Docusaurus configuration only.** Set `trailingSlash: false`. This makes
   newly generated links and sitemap entries canonical, but does not guarantee
   that old slash-suffixed requests redirect at the edge.
2. **Static `_redirects` rules only.** Add Pages redirect rules. This avoids a
   Worker, but a single rule cannot express the full dynamic path
   slash-removal policy as clearly as the existing advanced-mode pattern.
3. **Docusaurus plus an advanced-mode Worker (recommended).** Set
   `trailingSlash: false`, add a Worker that redirects every non-root path
   ending in `/`, and add a build audit that fails if generated internal URLs
   or sitemap entries end in `/`. This covers both newly generated addresses
   and legacy requests.

## Design

### Canonical generation

Set `trailingSlash: false` in `docs/site/docusaurus.config.ts`. Docusaurus will
generate slashless document URLs and corresponding `.html` output files. The
root URL `/` remains `/`, because it is the origin root rather than a
slash-suffixed document path.

### Cloudflare redirect

Add `docs/site/static/_worker.js`. The Worker will:

- leave `/` unchanged;
- for any other pathname ending in `/`, remove only the final slash;
- preserve the original query string;
- return HTTP 301 for the normalized request; and
- delegate all other requests to `env.ASSETS.fetch(request)`.

The Worker will not rewrite external hosts or alter paths that are already
canonical. It will be copied into `docs/site/build/_worker.js` by Docusaurus
and deployed with the existing Pages Direct Upload workflow.

### Generated-output audit

Add a small Node script under `docs/site/scripts/` and run it from `pnpm check`
after the build. The audit will inspect generated HTML and every generated
sitemap file. It will reject non-root internal URLs with a trailing slash in
HTML URL attributes, canonical/metadata URLs, or sitemap `<loc>` values. It
will ignore fragments, data URLs, external origins, and the root URL `/`.

This makes the URL policy part of the existing CI check instead of relying on
manual inspection after each docs change.

## Out of scope

- Redirecting external URLs, API URLs, or links to other domains.
- Changing the public docs domain or locale prefixes.
- Rewriting prose examples that are not generated site URLs.
- Adding a redirect for `/` to an empty path.

## Success criteria

- The production Docusaurus build succeeds with `trailingSlash: false`.
- Every generated sitemap URL is slashless except the root URL.
- Generated internal HTML URLs are slashless except the root URL.
- `/docs/example/` returns a 301 redirect to `/docs/example` while
  preserving its query string in Cloudflare Pages advanced mode.
- `/docs/example` and static assets are delegated to Pages unchanged.
- `pnpm check` fails if a future docs change reintroduces a trailing slash in a
  generated internal URL or sitemap entry.

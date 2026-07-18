import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';

const workerSource = await readFile(join('static', '_worker.js'), 'utf8');
const workerModule = await import(`data:text/javascript,${encodeURIComponent(workerSource)}`);
const worker = workerModule.default;
const delegatedRequests = [];

const env = {
  ASSETS: {
    fetch(request) {
      delegatedRequests.push(request.url);
      return Promise.resolve(new Response('asset', {status: 200}));
    },
  },
};

// The docs origin serves the Docusaurus build at its own root. The /docs
// prefix that appears on the public domain is added/stripped by the
// docs-proxy Worker, so here the origin only canonicalizes to slashless URLs.
const ORIGIN = 'https://cubeplex-docs.pages.dev';

// Trailing slash on a document → 301 to the slashless canonical, query kept.
const redirected = await worker.fetch(
  new Request(`${ORIGIN}/getting-started/quick-start/?source=legacy`),
  env,
);
assert.equal(redirected.status, 301);
assert.equal(
  redirected.headers.get('location'),
  `${ORIGIN}/getting-started/quick-start?source=legacy`,
);

// Locale home with a trailing slash → slashless.
const zhSlash = await worker.fetch(new Request(`${ORIGIN}/zh-Hans/`), env);
assert.equal(zhSlash.status, 301);
assert.equal(zhSlash.headers.get('location'), `${ORIGIN}/zh-Hans`);

// Canonical (slashless) requests are served, not redirected.
const canonical = await worker.fetch(new Request(`${ORIGIN}/getting-started/quick-start`), env);
assert.equal(canonical.status, 200);

// The docs home ('/') is served directly.
const root = await worker.fetch(new Request(`${ORIGIN}/`), env);
assert.equal(root.status, 200);

// Static assets pass straight through.
const asset = await worker.fetch(new Request(`${ORIGIN}/assets/js/main.js`), env);
assert.equal(asset.status, 200);

assert.deepEqual(delegatedRequests, [
  `${ORIGIN}/getting-started/quick-start`,
  `${ORIGIN}/`,
  `${ORIGIN}/assets/js/main.js`,
]);
console.log('Cloudflare URL normalization check passed.');

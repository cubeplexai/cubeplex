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

const redirected = await worker.fetch(
  new Request('https://docs.cubeplex.ai/docs/intro/?source=legacy'),
  env,
);
assert.equal(redirected.status, 301);
assert.equal(
  redirected.headers.get('location'),
  'https://docs.cubeplex.ai/docs/intro?source=legacy',
);

const root = await worker.fetch(new Request('https://docs.cubeplex.ai/?source=legacy'), env);
assert.equal(root.status, 301);
assert.equal(root.headers.get('location'), 'https://docs.cubeplex.ai/docs?source=legacy');

const zhRoot = await worker.fetch(
  new Request('https://docs.cubeplex.ai/zh-Hans/?source=legacy'),
  env,
);
assert.equal(zhRoot.status, 301);
assert.equal(
  zhRoot.headers.get('location'),
  'https://docs.cubeplex.ai/zh-Hans/docs?source=legacy',
);

const zhRootWithoutSlash = await worker.fetch(
  new Request('https://docs.cubeplex.ai/zh-Hans'),
  env,
);
assert.equal(zhRootWithoutSlash.status, 301);
assert.equal(zhRootWithoutSlash.headers.get('location'), 'https://docs.cubeplex.ai/zh-Hans/docs');

const canonical = await worker.fetch(new Request('https://docs.cubeplex.ai/docs/intro'), env);
assert.equal(canonical.status, 200);

const docsWelcome = await worker.fetch(new Request('https://docs.cubeplex.ai/docs'), env);
assert.equal(docsWelcome.status, 200);

const docsWelcomeWithSlash = await worker.fetch(
  new Request('https://docs.cubeplex.ai/docs/'),
  env,
);
assert.equal(docsWelcomeWithSlash.status, 301);
assert.equal(docsWelcomeWithSlash.headers.get('location'), 'https://docs.cubeplex.ai/docs');

const zhWelcome = await worker.fetch(
  new Request('https://docs.cubeplex.ai/zh-Hans/docs'),
  env,
);
assert.equal(zhWelcome.status, 200);

const zhWelcomeWithSlash = await worker.fetch(
  new Request('https://docs.cubeplex.ai/zh-Hans/docs/'),
  env,
);
assert.equal(zhWelcomeWithSlash.status, 301);
assert.equal(
  zhWelcomeWithSlash.headers.get('location'),
  'https://docs.cubeplex.ai/zh-Hans/docs',
);

assert.deepEqual(delegatedRequests, [
  'https://docs.cubeplex.ai/docs/intro',
  'https://docs.cubeplex.ai/docs.html',
  'https://docs.cubeplex.ai/zh-Hans/docs.html',
]);
console.log('Cloudflare URL normalization check passed.');

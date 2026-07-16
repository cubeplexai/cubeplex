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

const root = await worker.fetch(new Request('https://docs.cubeplex.ai/'), env);
assert.equal(root.status, 200);

const canonical = await worker.fetch(new Request('https://docs.cubeplex.ai/docs/intro'), env);
assert.equal(canonical.status, 200);

assert.deepEqual(delegatedRequests, [
  'https://docs.cubeplex.ai/',
  'https://docs.cubeplex.ai/docs/intro',
]);
console.log('Cloudflare URL normalization check passed.');

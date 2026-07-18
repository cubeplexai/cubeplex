// Cloudflare Pages advanced-mode Worker for the docs origin.
//
// The docs are published under /docs/* on the primary domain (cubeplex.ai)
// via the docs-proxy Worker, which strips the /docs prefix before the request
// reaches this origin. Here we only canonicalize to slashless URLs and serve
// assets; no path/prefix rewriting happens on the origin itself.
//
// Cloudflare's asset layer 308s bare directory paths (e.g. /zh-Hans → /zh-Hans/).
// If we also 301 the trailing slash back to slashless, that loops. So for a
// slashless request that the asset layer wants to redirect into a directory,
// we fetch the directory content internally and return it at the slashless URL.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname.length > 1 && url.pathname.endsWith('/')) {
      url.pathname = url.pathname.slice(0, -1);
      return Response.redirect(url.toString(), 301);
    }

    const response = await env.ASSETS.fetch(request);
    if (response.status < 300 || response.status >= 400) {
      return response;
    }

    const location = response.headers.get('Location');
    if (!location) return response;

    const target = new URL(location, url);
    // Directory trailing-slash redirect from the asset layer — serve it here.
    if (target.pathname === `${url.pathname}/`) {
      const dirUrl = new URL(url);
      dirUrl.pathname = `${url.pathname}/`;
      return env.ASSETS.fetch(new Request(dirUrl, request));
    }

    return response;
  },
};

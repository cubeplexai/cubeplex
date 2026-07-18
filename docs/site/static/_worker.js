// Cloudflare Pages advanced-mode Worker for the docs origin.
//
// The docs are published under /docs/* on the primary domain (cubeplex.ai)
// via the docs-proxy Worker, which strips the /docs prefix before the request
// reaches this origin. Here we only canonicalize to slashless URLs and serve
// assets; no path/prefix rewriting happens on the origin itself.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname.length > 1 && url.pathname.endsWith('/')) {
      url.pathname = url.pathname.slice(0, -1);
      return Response.redirect(url.toString(), 301);
    }

    return env.ASSETS.fetch(request);
  },
};

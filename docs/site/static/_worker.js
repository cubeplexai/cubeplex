// Cloudflare Pages advanced-mode Worker.
//
// Docusaurus generates slashless document URLs. Normalize old slash-suffixed
// requests at the edge before delegating canonical requests to Pages assets.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === '/') {
      url.pathname = '/docs';
      return Response.redirect(url.toString(), 301);
    }

    if (url.pathname === '/zh-Hans' || url.pathname === '/zh-Hans/') {
      url.pathname = '/zh-Hans/docs';
      return Response.redirect(url.toString(), 301);
    }

    if (url.pathname.length > 1 && url.pathname.endsWith('/')) {
      url.pathname = url.pathname.slice(0, -1);
      return Response.redirect(url.toString(), 301);
    }

    return env.ASSETS.fetch(request);
  },
};

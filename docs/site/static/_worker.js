// Cloudflare Pages "advanced mode" Worker.
//
// The site moved from the default *.pages.dev hostname to the custom domain
// cubepi.ai, but only the root path was redirecting — deep pages still served
// 200 on cubepi.pages.dev (duplicate content on two domains). This 301-redirects
// the production pages.dev hostname to the matching cubepi.ai path so the domain
// migration sends search engines one clean 1:1 signal.
//
// Why _worker.js (advanced mode) and not functions/_middleware.js:
//   The site is deployed via Direct Upload (cloudflare/pages-action uploads
//   website/build). Cloudflare compiles a _worker.js that sits IN the uploaded
//   output directory; a functions/ directory, by contrast, must live at the
//   project root where Wrangler runs — placing it under static/ (so it lands in
//   build/functions/) makes Cloudflare serve it as a static file, not a Function.
//   Docusaurus copies static/_worker.js to build/_worker.js, the deployed dir.
//
// Notes:
//   - Matches the production hostname exactly, so preview deployments
//     (e.g. <hash>.cubepi.pages.dev) fall through and stay browsable.
//   - Everything else is forwarded to env.ASSETS.fetch, which serves the static
//     site exactly as Pages would (trailing-slash handling, 404.html, etc.).
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname === "cubepi.pages.dev") {
      url.hostname = "cubepi.ai";
      url.protocol = "https:";
      url.port = "";
      return Response.redirect(url.toString(), 301);
    }
    return env.ASSETS.fetch(request);
  },
};

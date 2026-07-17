# Docs URL normalization implementation note

`trailingSlash: false` makes document routes and normal links slashless, but
Docusaurus still emits a trailing slash for locale roots such as
`/zh-Hans/` and the localized docs landing route `/zh-Hans/docs/`. Those are
internal URLs, so the Cloudflare Worker alone is not enough: the generated
HTML and sitemap would still advertise the non-canonical form.

The build now runs a post-build normalizer over generated HTML and XML. It
removes the final slash only from same-origin, non-root URLs. The following
audit then checks the normalized output and fails on future regressions.

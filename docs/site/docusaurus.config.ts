import type { Config } from '@docusaurus/types';
import type { Options as ClassicOptions } from '@docusaurus/preset-classic';
import { themes as prismThemes } from 'prism-react-renderer';

/** Raise priority for docs entry + quick-start; leave the rest at default. */
function sitemapPriority(url: string): number {
  try {
    const path = new URL(url).pathname.replace(/\/+$/, '') || '/';
    // EN + zh-Hans home
    if (path === '/docs' || path === '/docs/zh-Hans') return 1.0;
    // Primary onboarding page
    if (
      path === '/docs/getting-started/quick-start' ||
      path === '/docs/zh-Hans/getting-started/quick-start'
    ) {
      return 0.9;
    }
    if (path.startsWith('/docs/getting-started') || path.startsWith('/docs/zh-Hans/getting-started')) {
      return 0.8;
    }
  } catch {
    // fall through
  }
  return 0.5;
}

const classicOptions: ClassicOptions = {
  docs: {
    // Docs render at the site root of the Docusaurus app; combined with the
    // '/docs/' baseUrl below this yields public URLs of cubeplex.ai/docs/*
    // (served on the main domain via the docs-proxy Worker).
    routeBasePath: '/',
    sidebarPath: './sidebars.ts',
    editUrl: 'https://github.com/cubeplexai/cubeplex/edit/main/docs/site/',
  },
  blog: false,
  theme: {
    customCss: './src/css/custom.css',
  },
  sitemap: {
    lastmod: 'date',
    changefreq: 'weekly',
    priority: 0.5,
    async createSitemapItems(params) {
      const items = await params.defaultCreateSitemapItems(params);
      return items.map((item) => ({
        ...item,
        priority: sitemapPriority(item.url),
      }));
    },
  },
};

const config: Config = {
  title: 'CubePlex',
  tagline: 'Your AI agent workspace — chat, automate, extend',
  favicon: 'img/cubeplex-favicon.svg',

  // Served from the main domain under /docs (same-origin as the marketing
  // site). baseUrl namespaces every page + asset under /docs/ so the
  // docs-proxy Worker route `cubeplex.ai/docs*` captures all of it.
  url: 'https://cubeplex.ai',
  baseUrl: '/docs/',
  trailingSlash: false,
  organizationName: 'cubeplexai',
  projectName: 'cubeplex',

  onBrokenLinks: 'throw',
  onBrokenAnchors: 'throw',

  markdown: {
    hooks: {
      onBrokenMarkdownLinks: 'throw',
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en', 'zh-Hans'],
    localeConfigs: {
      en:        { label: 'English' },
      'zh-Hans': { label: '简体中文' },
    },
  },

  headTags: [
    {
      tagName: 'script',
      attributes: { type: 'application/ld+json' },
      innerHTML: JSON.stringify({
        '@context': 'https://schema.org',
        '@graph': [
          {
            '@type': 'Organization',
            '@id': 'https://cubeplex.ai/#organization',
            name: 'CubePlex',
            url: 'https://cubeplex.ai',
            logo: 'https://cubeplex.ai/docs/img/cubeplex-favicon.svg',
          },
          {
            '@type': 'WebSite',
            '@id': 'https://cubeplex.ai/docs#website',
            name: 'CubePlex Documentation',
            url: 'https://cubeplex.ai/docs',
            inLanguage: ['en', 'zh-Hans'],
            publisher: { '@id': 'https://cubeplex.ai/#organization' },
          },
          {
            '@type': 'TechArticle',
            '@id': 'https://cubeplex.ai/docs#docs',
            headline: 'CubePlex Documentation',
            description:
              'Guides for CubePlex — self-hosted AI agent workspace: setup, conversations, skills, MCP, memory, and admin.',
            url: 'https://cubeplex.ai/docs',
            // Reuse the marketing OG image (same brand card; avoids shipping a large binary in docs).
            image: 'https://cubeplex.ai/og.png',
            isPartOf: { '@id': 'https://cubeplex.ai/docs#website' },
            publisher: { '@id': 'https://cubeplex.ai/#organization' },
          },
        ],
      }),
    },
  ],

  presets: [['classic', classicOptions]],

  themeConfig: {
    // Absolute URL so social cards reuse the marketing OG asset without
    // duplicating a large PNG under docs/site (pre-commit size limit).
    image: 'https://cubeplex.ai/og.png',
    metadata: [
      { name: 'keywords', content: 'CubePlex, AI agent, AI workspace, agent platform, chat AI, MCP tools, AI automation' },
      { name: 'twitter:card', content: 'summary_large_image' },
    ],
    navbar: {
      logo: {
        alt: 'CubePlex',
        src: 'img/cubeplex-lockup-on-light.svg',
        srcDark: 'img/cubeplex-lockup-on-dark.svg',
        href: 'https://cubeplex.ai',
        width: 140,
        height: 32,
      },
      items: [
        { type: 'docSidebar', sidebarId: 'docs', label: 'Docs', position: 'left' },
        { type: 'localeDropdown', position: 'right' },
        {
          href: 'https://github.com/cubeplexai/cubeplex',
          position: 'right',
          className: 'header-github-link',
          'aria-label': 'GitHub repository',
        },
      ],
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
    },
    colorMode: { defaultMode: 'light', respectPrefersColorScheme: true },
  },
};

export default config;

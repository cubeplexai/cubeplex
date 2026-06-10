import * as fs from 'fs';
import * as path from 'path';

import type { Config } from '@docusaurus/types';
import type { Options as ClassicOptions } from '@docusaurus/preset-classic';
import { themes as prismThemes } from 'prism-react-renderer';

// Use `||` not `??` here: GitHub Actions expands an unset secret to "" (an
// empty string passes the nullish check, so `??` would leave api_host empty
// and PostHog falls back to the current origin — sending POST /e/ to the
// docs domain and hitting a 405 from Cloudflare Pages).
const POSTHOG_KEY = process.env.POSTHOG_KEY || '';
const POSTHOG_HOST = process.env.POSTHOG_HOST || 'https://us.i.posthog.com';
const GIT_SHA = process.env.GITHUB_SHA?.slice(0, 7) ?? 'dev';

// Single source of truth for the package version shown in the homepage
// MetaBar: read pyproject.toml at config-load time so the site never
// drifts from the actual released version. Plain regex parse — avoids
// a TOML-parser dep just for one field.
const PYPROJECT_TOML = fs.readFileSync(
  path.join(__dirname, '..', 'pyproject.toml'),
  'utf-8',
);
const VERSION_MATCH = PYPROJECT_TOML.match(/^version\s*=\s*"([^"]+)"/m);
const PACKAGE_VERSION = VERSION_MATCH ? VERSION_MATCH[1] : 'dev';

const classicOptions: ClassicOptions = {
  docs: {
    sidebarPath: './sidebars.ts',
    editUrl: 'https://github.com/cubeplexai/cubepi/edit/main/website/',
    lastVersion: '0.10',
    versions: {
      current: { label: 'Next 🚧', path: 'next', banner: 'unreleased', noIndex: true },
      '0.10':  { label: '0.10 (latest)', path: '' },
      '0.9':   { label: '0.9', path: '0.9', noIndex: true },
      '0.8':   { label: '0.8', path: '0.8', noIndex: true },
      '0.7':   { label: '0.7', path: '0.7', noIndex: true },
      '0.6':   { label: '0.6', path: '0.6', noIndex: true },
      '0.5':   { label: '0.5', path: '0.5', noIndex: true },
      '0.4':   { label: '0.4', path: '0.4', noIndex: true },
      '0.3':   { label: '0.3', path: '0.3', noIndex: true },
    },
  },
  blog: false,
  theme: {
    customCss: './src/css/custom.css',
  },
  sitemap: {
    lastmod: 'date',
    changefreq: 'weekly',
    priority: 0.5,
    ignorePatterns: [
      '/docs/next/**',
      '/docs/0.3/**',
      '/docs/0.4/**',
      '/docs/0.5/**',
      '/docs/0.6/**',
      '/docs/0.7/**',
      '/docs/0.8/**',
      '/docs/0.9/**',
    ],
  },
};

const config: Config = {
  title: 'CubePi',
  tagline: 'A Pythonic, async-native agent framework — an alternative to langgraph and pi-agent-core',
  favicon: 'img/brand/cubepi-logo.svg',

  url: 'https://cubepi.ai',
  baseUrl: '/',
  organizationName: 'cubeplexai',
  projectName: 'cubepi',

  onBrokenLinks: 'throw',
  onBrokenAnchors: 'throw',
  onBrokenMarkdownLinks: 'throw',

  i18n: {
    defaultLocale: 'en',
    locales: ['en', 'zh-Hans'],
    localeConfigs: {
      en:        { label: 'English' },
      'zh-Hans': { label: '简体中文' },
    },
  },

  // Site-wide structured data: an Organization is the only schema that is
  // genuinely true on *every* page. The product-level SoftwareApplication
  // schema is scoped to the homepage (see src/pages/index.tsx) so docs
  // subpages aren't all mislabeled as the application itself.
  headTags: [
    {
      tagName: 'meta',
      attributes: { name: 'baidu-site-verification', content: 'codeva-fPG0VGR2p8' },
    },
    {
      tagName: 'script',
      attributes: { type: 'application/ld+json' },
      innerHTML: JSON.stringify({
        '@context': 'https://schema.org',
        '@type': 'Organization',
        name: 'CubePi',
        url: 'https://cubepi.ai',
        logo: 'https://cubepi.ai/img/brand/cubepi-logo.png',
        sameAs: [
          'https://github.com/cubeplexai/cubepi',
          'https://x.com/cubeplexai',
          'https://pypi.org/project/cubepi/',
        ],
      }),
    },
  ],

  customFields: { POSTHOG_KEY, POSTHOG_HOST, GIT_SHA, PACKAGE_VERSION },

  clientModules: [require.resolve('./src/clientModules/posthog.ts')],

  markdown: {
    // Strip the ## [Unreleased] heading only when the section is empty — i.e.
    // immediately followed by the next release heading (`## [x.y.z]`). The
    // lookahead requires "## " with a trailing space so a populated section
    // whose first child is a "### Added" subsection is NOT stripped (### also
    // starts with ##), which would otherwise drop the heading but keep its body.
    preprocessor: ({fileContent}) =>
      fileContent.replace(/^## \[Unreleased\]\n+(?=## )/m, ''),
  },

  presets: [['classic', classicOptions]],

  plugins: [
    [
      '@docusaurus/plugin-google-gtag',
      {
        trackingID: 'G-NE2PDN0M91',
        anonymizeIP: true,
      },
    ],
  ],

  themeConfig: {
    metadata: [
      { name: 'keywords', content: 'CubePi, langgraph alternative, pi-agent-core alternative, Python agent framework, async agent, LLM agent, AI agent framework, tool-use agent, Python Agent 框架, 异步 Agent, langgraph 替代品, AI Agent 开发' },
      { name: 'twitter:site', content: '@cubeplexai' },
      { name: 'twitter:creator', content: '@cubeplexai' },
    ],
    image: 'img/brand/cubepi-social-preview.png',
    navbar: {
      title: 'CubePi',
      logo: { alt: 'CubePi logo', src: 'img/brand/cubepi-logo.svg' },
      items: [
        // Version-aware section links (see src/components/VersionAwareDocLink):
        // active state is path-driven so only one lights up per page, and the
        // target stays within the version the reader is currently browsing.
        { type: 'custom-versionAwareDocLink', section: 'docs', label: 'Docs', position: 'left' },
        { type: 'custom-versionAwareDocLink', section: 'api', label: 'API', position: 'left' },
        { type: 'custom-versionAwareDocLink', section: 'recipes', label: 'Recipes', position: 'left' },
        {
          type: 'dropdown',
          label: 'Compare',
          position: 'left',
          items: [
            { to: '/compare/langgraph', label: 'vs LangGraph' },
            { to: '/compare/crewai', label: 'vs CrewAI' },
            { to: '/compare/pydantic-ai', label: 'vs PydanticAI' },
            { to: '/compare/pi-agent-core', label: 'vs pi-agent-core' },
          ],
        },
        { to: '/changelog', label: 'Changelog', position: 'left' },
        { to: '/faq', label: 'FAQ', position: 'left' },
        { type: 'docsVersionDropdown', position: 'right' },
        { type: 'localeDropdown', position: 'right' },
        {
          href: 'https://github.com/cubeplexai/cubepi',
          position: 'right',
          className: 'header-github-link',
          'aria-label': 'GitHub repository',
        },
      ],
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['python', 'bash', 'toml'],
    },
    colorMode: { defaultMode: 'light', respectPrefersColorScheme: true },
  },
};

export default config;

import type { Config } from '@docusaurus/types';
import type { Options as ClassicOptions } from '@docusaurus/preset-classic';
import { themes as prismThemes } from 'prism-react-renderer';

const classicOptions: ClassicOptions = {
  docs: {
    sidebarPath: './sidebars.ts',
    editUrl: 'https://github.com/cubeplexai/cubebox/edit/main/docs/site/',
  },
  blog: false,
  theme: {
    customCss: './src/css/custom.css',
  },
  sitemap: {
    lastmod: 'date',
    changefreq: 'weekly',
    priority: 0.5,
  },
};

const config: Config = {
  title: 'CubeBox',
  tagline: 'Your AI agent workspace — chat, automate, extend',
  favicon: 'img/favicon.ico',

  url: 'https://docs.cubebox.ai',
  baseUrl: '/',
  organizationName: 'cubeplexai',
  projectName: 'cubebox',

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
        '@type': 'Organization',
        name: 'CubeBox',
        url: 'https://cubebox.ai',
      }),
    },
  ],

  presets: [['classic', classicOptions]],

  themeConfig: {
    metadata: [
      { name: 'keywords', content: 'CubeBox, AI agent, AI workspace, agent platform, chat AI, MCP tools, AI automation' },
    ],
    navbar: {
      title: 'CubeBox',
      items: [
        { type: 'docSidebar', sidebarId: 'docs', label: 'Docs', position: 'left' },
        { type: 'localeDropdown', position: 'right' },
        {
          href: 'https://github.com/cubeplexai/cubebox',
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

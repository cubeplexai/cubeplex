# CubePlex User Documentation Site — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a Docusaurus 3.10 documentation site at `docs/site/` forked from cubepi's `website/`, with 24 English user-facing docs across 7 sections.

**Architecture:** Copy cubepi `website/` skeleton → strip cubepi-specific content (versioned docs, compare pages, API ref generator, unused homepage components) → rewrite config/brand for CubePlex → create simplified homepage (Hero + FeatureGrid) → write all 24 markdown docs → verify build.

**Tech Stack:** Docusaurus 3.10, React 19, TypeScript, pnpm, CSS Modules

**Source reference:** `/home/chris/cubepi/website/` — the cubepi docs site to fork from.

---

## Task 1: Copy cubepi website skeleton and strip unwanted content

**Files:**
- Create: `docs/site/` (entire directory tree)

- [ ] **Step 1: Copy the cubepi website directory**

```bash
cp -r /home/chris/cubepi/website /home/chris/cubeplex/docs/site
```

- [ ] **Step 2: Remove versioned docs, sidebars, and versions.json**

```bash
rm -rf /home/chris/cubeplex/docs/site/versioned_docs
rm -rf /home/chris/cubeplex/docs/site/versioned_sidebars
rm -f /home/chris/cubeplex/docs/site/versions.json
```

- [ ] **Step 3: Remove compare pages, FAQ, changelog**

```bash
rm -rf /home/chris/cubeplex/docs/site/src/pages/compare
rm -f /home/chris/cubeplex/docs/site/src/pages/faq.tsx
rm -f /home/chris/cubeplex/docs/site/src/pages/changelog.mdx
```

- [ ] **Step 4: Remove API reference generator and docs**

```bash
rm -rf /home/chris/cubeplex/docs/site/scripts
rm -rf /home/chris/cubeplex/docs/site/docs/api
```

- [ ] **Step 5: Remove unused homepage components**

```bash
rm -f /home/chris/cubeplex/docs/site/src/components/Home/WhyTable.tsx
rm -f /home/chris/cubeplex/docs/site/src/components/Home/WhyTable.module.css
rm -f /home/chris/cubeplex/docs/site/src/components/Home/HelloAgent.tsx
rm -f /home/chris/cubeplex/docs/site/src/components/Home/HelloAgent.module.css
rm -f /home/chris/cubeplex/docs/site/src/components/Home/InstallMatrix.tsx
rm -f /home/chris/cubeplex/docs/site/src/components/Home/InstallMatrix.module.css
rm -f /home/chris/cubeplex/docs/site/src/components/Home/MetaBar.tsx
rm -f /home/chris/cubeplex/docs/site/src/components/Home/MetaBar.module.css
```

- [ ] **Step 6: Remove VersionAwareDocLink and Compare components**

```bash
rm -f /home/chris/cubeplex/docs/site/src/components/VersionAwareDocLink.tsx
rm -f /home/chris/cubeplex/docs/site/src/components/VersionAwareDocLink.test.tsx
rm -f /home/chris/cubeplex/docs/site/src/components/versionAwareDocLinkConfig.ts
rm -rf /home/chris/cubeplex/docs/site/src/components/Compare
rm -rf /home/chris/cubeplex/docs/site/src/components/HomepageFeatures
rm -f /home/chris/cubeplex/docs/site/src/pages/index.module.css
```

- [ ] **Step 7: Remove all cubepi doc content**

```bash
rm -rf /home/chris/cubeplex/docs/site/docs
mkdir -p /home/chris/cubeplex/docs/site/docs
```

- [ ] **Step 8: Remove i18n translated docs (keep structure for later)**

```bash
rm -rf /home/chris/cubeplex/docs/site/i18n
```

- [ ] **Step 9: Remove cubepi brand images (keep fonts)**

```bash
rm -f /home/chris/cubeplex/docs/site/static/img/brand/cubepi-*
rm -f /home/chris/cubeplex/docs/site/static/img/docusaurus-social-card.jpg
rm -f /home/chris/cubeplex/docs/site/static/img/favicon.ico
rm -f /home/chris/cubeplex/docs/site/static/llms.txt
```

- [ ] **Step 10: Remove pnpm-lock.yaml (will regenerate)**

```bash
rm -f /home/chris/cubeplex/docs/site/pnpm-lock.yaml
rm -f /home/chris/cubeplex/docs/site/pnpm-workspace.yaml
```

- [ ] **Step 11: Commit**

```bash
git add docs/site/
git commit -m "chore(docs): copy cubepi website skeleton, strip cubepi-specific content"
```

---

## Task 2: Rewrite docusaurus.config.ts for CubePlex

**Files:**
- Modify: `docs/site/docusaurus.config.ts`

- [ ] **Step 1: Rewrite the config file**

Replace the entire content of `docs/site/docusaurus.config.ts` with:

```ts
import type { Config } from '@docusaurus/types';
import type { Options as ClassicOptions } from '@docusaurus/preset-classic';
import { themes as prismThemes } from 'prism-react-renderer';

const classicOptions: ClassicOptions = {
  docs: {
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
  },
};

const config: Config = {
  title: 'CubePlex',
  tagline: 'Your AI agent workspace — chat, automate, extend',
  favicon: 'img/favicon.ico',

  url: 'https://docs.cubeplex.ai',
  baseUrl: '/',
  organizationName: 'cubeplexai',
  projectName: 'cubeplex',

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

  headTags: [
    {
      tagName: 'script',
      attributes: { type: 'application/ld+json' },
      innerHTML: JSON.stringify({
        '@context': 'https://schema.org',
        '@type': 'Organization',
        name: 'CubePlex',
        url: 'https://cubeplex.ai',
      }),
    },
  ],

  presets: [['classic', classicOptions]],

  themeConfig: {
    metadata: [
      { name: 'keywords', content: 'CubePlex, AI agent, AI workspace, agent platform, chat AI, MCP tools, AI automation' },
    ],
    navbar: {
      title: 'CubePlex',
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
```

- [ ] **Step 2: Commit**

```bash
git add docs/site/docusaurus.config.ts
git commit -m "chore(docs): rewrite docusaurus.config.ts for CubePlex"
```

---

## Task 3: Rewrite package.json

**Files:**
- Modify: `docs/site/package.json`

- [ ] **Step 1: Rewrite package.json**

Replace the entire content of `docs/site/package.json` with:

```json
{
  "name": "cubeplex-docs",
  "version": "0.0.0",
  "private": true,
  "scripts": {
    "docusaurus": "docusaurus",
    "start": "docusaurus start",
    "build": "docusaurus build",
    "swizzle": "docusaurus swizzle",
    "clear": "docusaurus clear",
    "serve": "docusaurus serve",
    "write-translations": "docusaurus write-translations",
    "typecheck": "tsc",
    "check": "pnpm build && pnpm typecheck",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "@docusaurus/core": "3.10.1",
    "@docusaurus/faster": "3.10.1",
    "@docusaurus/preset-classic": "3.10.1",
    "@mdx-js/react": "^3.0.0",
    "clsx": "^2.0.0",
    "posthog-js": "^1.373.4",
    "prism-react-renderer": "^2.3.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "@docusaurus/module-type-aliases": "3.10.1",
    "@docusaurus/tsconfig": "3.10.1",
    "@docusaurus/types": "3.10.1",
    "@testing-library/jest-dom": "^6.9.1",
    "@testing-library/react": "^16.3.2",
    "@types/react": "^19.0.0",
    "@vitejs/plugin-react": "^4.7.0",
    "jsdom": "^24.1.3",
    "typescript": "~6.0.2",
    "vite": "^7.1.5",
    "vitest": "^4.1.0"
  },
  "browserslist": {
    "production": [">0.5%", "not dead", "not op_mini all"],
    "development": ["last 3 chrome version", "last 3 firefox version", "last 5 safari version"]
  },
  "engines": {
    "node": ">=20.0"
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add docs/site/package.json
git commit -m "chore(docs): rewrite package.json for CubePlex"
```

---

## Task 4: Rewrite sidebar config

**Files:**
- Modify: `docs/site/sidebars.ts`

- [ ] **Step 1: Rewrite sidebars.ts**

Replace the entire content of `docs/site/sidebars.ts` with:

```ts
import type { SidebarsConfig } from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docs: [
    'intro',
    {
      type: 'category',
      label: 'Getting Started',
      collapsed: false,
      items: [
        'getting-started/quick-start',
        'getting-started/core-concepts',
        'getting-started/workspace-setup',
      ],
    },
    {
      type: 'category',
      label: 'Guides',
      items: [
        {
          type: 'category',
          label: 'Conversations',
          items: [
            'guides/conversations/basics',
            'guides/conversations/attachments',
            'guides/conversations/artifacts',
            'guides/conversations/model-selection',
          ],
        },
        {
          type: 'category',
          label: 'Skills',
          items: [
            'guides/skills/overview',
            'guides/skills/discover-and-install',
            'guides/skills/managing-skills',
          ],
        },
        {
          type: 'category',
          label: 'Memory',
          items: [
            'guides/memory/overview',
            'guides/memory/using-memory',
            'guides/memory/managing-memory',
          ],
        },
        {
          type: 'category',
          label: 'MCP Tools',
          items: [
            'guides/mcp/overview',
            'guides/mcp/installing-connectors',
            'guides/mcp/using-tools',
          ],
        },
        {
          type: 'category',
          label: 'Automation',
          items: [
            'guides/automation/scheduled-tasks',
            'guides/automation/event-triggers',
          ],
        },
      ],
    },
    {
      type: 'category',
      label: 'Administration',
      items: [
        'admin/models',
        'admin/members',
        'admin/mcp-connectors',
        'admin/skills-management',
        'admin/sandbox',
        'admin/cost-tracking',
      ],
    },
  ],
};

export default sidebars;
```

- [ ] **Step 2: Commit**

```bash
git add docs/site/sidebars.ts
git commit -m "chore(docs): add CubePlex sidebar config"
```

---

## Task 5: Simplify homepage and NavbarItem theme

**Files:**
- Modify: `docs/site/src/pages/index.tsx`
- Modify: `docs/site/src/theme/NavbarItem/ComponentTypes.tsx`

- [ ] **Step 1: Rewrite index.tsx**

Replace the entire content of `docs/site/src/pages/index.tsx` with:

```tsx
import React from 'react';
import Head from '@docusaurus/Head';
import Layout from '@theme/Layout';
import Hero from '@site/src/components/Home/Hero';
import FeatureGrid from '@site/src/components/Home/FeatureGrid';

export default function Home(): React.ReactElement {
  const softwareJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'SoftwareApplication',
    name: 'CubePlex',
    description: 'Your AI agent workspace — chat, automate, extend.',
    url: 'https://cubeplex.ai',
    applicationCategory: 'BusinessApplication',
    offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
    author: { '@type': 'Organization', name: 'CubePlex', url: 'https://cubeplex.ai' },
  };

  return (
    <Layout
      title="Your AI agent workspace"
      description="CubePlex is an AI agent workspace — multi-model chat, skills, memory, MCP tools, and automation in one platform."
    >
      <Head>
        <script type="application/ld+json">{JSON.stringify(softwareJsonLd)}</script>
      </Head>
      <Hero />
      <FeatureGrid />
    </Layout>
  );
}
```

- [ ] **Step 2: Simplify NavbarItem ComponentTypes**

Replace the entire content of `docs/site/src/theme/NavbarItem/ComponentTypes.tsx` with:

```tsx
import ComponentTypes from '@theme-original/NavbarItem/ComponentTypes';

export default {
  ...ComponentTypes,
};
```

- [ ] **Step 3: Commit**

```bash
git add docs/site/src/pages/index.tsx docs/site/src/theme/NavbarItem/ComponentTypes.tsx
git commit -m "chore(docs): simplify homepage to Hero + FeatureGrid"
```

---

## Task 6: Rewrite Hero component for CubePlex

**Files:**
- Modify: `docs/site/src/components/Home/Hero.tsx`
- Modify: `docs/site/src/components/Home/Hero.module.css`

- [ ] **Step 1: Rewrite Hero.tsx**

Replace the entire content of `docs/site/src/components/Home/Hero.tsx` with:

```tsx
import React from 'react';
import Link from '@docusaurus/Link';
import { useIsZhHans } from '@site/src/hooks/useIsZhHans';
import styles from './Hero.module.css';

export default function Hero() {
  const zh = useIsZhHans();

  return (
    <section className={styles.hero}>
      <h1 className={styles.h1}>
        CubePlex
        <span className={styles.h1sub}>
          {zh
            ? '你的 AI Agent 工作空间 — 对话、自动化、扩展。'
            : 'Your AI agent workspace — chat, automate, extend.'}
        </span>
      </h1>
      <p className={styles.lead}>
        {zh ? (
          <>
            CubePlex 是一个全功能 AI agent 平台。多模型对话、技能市场、三层记忆、
            MCP 工具集成、定时任务与 Webhook 自动化 — 一站式管理你的 AI 工作流。
          </>
        ) : (
          <>
            CubePlex is a full-featured AI agent platform. Multi-model conversations,
            a skills marketplace, three-tier memory, MCP tool integration, scheduled
            tasks, and webhook automation — manage your AI workflows in one place.
          </>
        )}
      </p>
      <div className={styles.actions}>
        <Link className={`${styles.cta} ${styles.ctaPrimary}`} to="/docs/getting-started/quick-start">
          {zh ? '快速开始 →' : 'Get Started →'}
        </Link>
        <a className={`${styles.cta} ${styles.ctaGhost}`} href="https://github.com/cubeplexai/cubeplex" target="_blank" rel="noopener noreferrer">
          GitHub
        </a>
      </div>
    </section>
  );
}
```

- [ ] **Step 2: Simplify Hero.module.css**

Replace the entire content of `docs/site/src/components/Home/Hero.module.css` with:

```css
.hero { max-width: 880px; margin: 64px auto 64px; padding: 0 24px; text-align: left; }
.h1 { font-family: 'Inter Tight', 'Inter', system-ui; font-size: 52px; line-height: 1.05; letter-spacing: -0.03em; font-weight: 600; color: var(--ink-12); margin: 0 0 20px; }
.h1sub { display: block; margin-top: 12px; font-family: 'Inter Tight', 'Inter', system-ui; font-size: 22px; line-height: 1.35; letter-spacing: -0.01em; font-style: italic; font-weight: 400; color: var(--ink-11); }
.lead { font-size: 16px; line-height: 1.55; color: var(--ink-9); margin: 0 0 28px; max-width: 640px; }
.actions { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.cta { display: inline-flex; align-items: center; gap: 10px; height: 36px; padding: 0 14px; border: 1px solid var(--ink-5); border-radius: 5px; background: var(--surface); font-size: 14px; color: var(--ink-11); font-weight: 500; cursor: pointer; text-decoration: none; }
.cta:hover { background: var(--ink-3); text-decoration: none; color: var(--ink-11); }
.ctaPrimary { background: var(--ink-12); border-color: var(--ink-12); color: var(--surface); }
.ctaPrimary:hover { background: var(--ink-11); color: var(--surface); }
.ctaGhost { background: transparent; }
@media (max-width: 640px) { .h1 { font-size: 36px; } .h1sub { font-size: 18px; } .hero { margin-top: 32px; } }
```

- [ ] **Step 3: Commit**

```bash
git add docs/site/src/components/Home/Hero.tsx docs/site/src/components/Home/Hero.module.css
git commit -m "feat(docs): CubePlex Hero component"
```

---

## Task 7: Rewrite FeatureGrid component for CubePlex

**Files:**
- Modify: `docs/site/src/components/Home/FeatureGrid.tsx`

The `FeatureGrid.module.css` from cubepi is kept as-is (grid layout is unchanged).

- [ ] **Step 1: Rewrite FeatureGrid.tsx**

Replace the entire content of `docs/site/src/components/Home/FeatureGrid.tsx` with:

```tsx
import React from 'react';
import Link from '@docusaurus/Link';
import { useIsZhHans } from '@site/src/hooks/useIsZhHans';
import styles from './FeatureGrid.module.css';

type Icon = React.FC<React.SVGProps<SVGSVGElement>>;

const IconChat: Icon = (p) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M2 3h12v8H5l-3 3V3z" />
  </svg>
);
const IconPuzzle: Icon = (p) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M6 2v2a2 2 0 104 0V2h4v4h-2a2 2 0 100 4h2v4H6v-2a2 2 0 10-4 0v2H2V2h4z" />
  </svg>
);
const IconBrain: Icon = (p) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M8 1C5.2 1 3 3.2 3 6c0 1.8.9 3.3 2.3 4.2.2.1.2.3.2.5V14h5v-3.3c0-.2.1-.4.2-.5C12.1 9.3 13 7.8 13 6c0-2.8-2.2-5-5-5z" />
    <path d="M6 14h4" />
  </svg>
);
const IconPlug: Icon = (p) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M5 1v4M11 1v4M3 5h10v4a4 4 0 01-4 4H7a4 4 0 01-4-4V5zM8 13v2" />
  </svg>
);
const IconClock: Icon = (p) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <circle cx="8" cy="8" r="6" />
    <path d="M8 4v4l3 2" />
  </svg>
);
const IconShield: Icon = (p) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M8 1L2 4v4c0 3.3 2.6 6.4 6 7 3.4-.6 6-3.7 6-7V4L8 1z" />
  </svg>
);

const CARDS_EN = [
  { Icon: IconChat,    title: 'Conversations',  body: 'Multi-model chat with file attachments and artifact generation.',  href: '/docs/guides/conversations/basics' },
  { Icon: IconPuzzle,  title: 'Skills',          body: 'Discover and install agent capabilities in one click.',            href: '/docs/guides/skills/overview' },
  { Icon: IconBrain,   title: 'Memory',          body: 'Three-tier memory — the agent learns as you work.',               href: '/docs/guides/memory/overview' },
  { Icon: IconPlug,    title: 'MCP Tools',       body: 'Connect external services; the agent calls APIs for you.',        href: '/docs/guides/mcp/overview' },
  { Icon: IconClock,   title: 'Automation',       body: 'Scheduled tasks + webhook triggers for hands-free operation.',    href: '/docs/guides/automation/scheduled-tasks' },
  { Icon: IconShield,  title: 'Administration',   body: 'Model management, team roles, cost tracking, sandbox policies.', href: '/docs/admin/models' },
];

const CARDS_ZH = [
  { Icon: IconChat,    title: '对话',       body: '多模型对话，文件附件，Artifact 生成。',         href: '/docs/guides/conversations/basics' },
  { Icon: IconPuzzle,  title: '技能',       body: '一键发现安装，扩展 Agent 能力。',               href: '/docs/guides/skills/overview' },
  { Icon: IconBrain,   title: '记忆',       body: '三层记忆 — Agent 越用越懂你。',                href: '/docs/guides/memory/overview' },
  { Icon: IconPlug,    title: 'MCP 工具',   body: '连接外部服务，Agent 替你调 API。',             href: '/docs/guides/mcp/overview' },
  { Icon: IconClock,   title: '自动化',     body: '定时任务 + Webhook 触发，无人值守运行。',       href: '/docs/guides/automation/scheduled-tasks' },
  { Icon: IconShield,  title: '管理',       body: '模型管理、团队角色、费用追踪、沙箱策略。',      href: '/docs/admin/models' },
];

export default function FeatureGrid() {
  const zh = useIsZhHans();
  const CARDS = zh ? CARDS_ZH : CARDS_EN;
  return (
    <section className={styles.section}>
      <div className={styles.grid}>
        {CARDS.map((c) => (
          <Link key={c.title} to={c.href} className={styles.card}>
            <c.Icon className={styles.icon} width={16} height={16} />
            <h3 className={styles.title}>{c.title}</h3>
            <p className={styles.body}>{c.body}</p>
          </Link>
        ))}
      </div>
    </section>
  );
}
```

- [ ] **Step 2: Update FeatureGrid.module.css — change grid to 3×2**

Replace the entire content of `docs/site/src/components/Home/FeatureGrid.module.css` with:

```css
.section { max-width: 1080px; margin: 0 auto 64px; padding: 0 24px; }
.grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.card { display: flex; flex-direction: column; gap: 6px; padding: 18px 16px 14px; border: 1px solid var(--ink-5); border-radius: 6px; background: var(--surface); color: inherit; text-decoration: none; position: relative; min-height: 120px; }
.card:hover { border-color: var(--ink-7); background: var(--ink-3); text-decoration: none; color: inherit; }
.icon { color: var(--ink-9); }
.title { font-family: 'Inter Tight'; font-size: 15px; font-weight: 600; letter-spacing: -0.005em; color: var(--ink-12); margin: 4px 0 0; }
.body { font-size: 13px; color: var(--ink-9); line-height: 1.5; margin: 0; }
@media (max-width: 900px) { .grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 560px) { .grid { grid-template-columns: 1fr; } }
```

- [ ] **Step 3: Commit**

```bash
git add docs/site/src/components/Home/FeatureGrid.tsx docs/site/src/components/Home/FeatureGrid.module.css
git commit -m "feat(docs): CubePlex FeatureGrid component (6 tiles)"
```

---

## Task 8: Update PostHog client module and static assets

**Files:**
- Modify: `docs/site/src/clientModules/posthog.ts`
- Modify: `docs/site/static/robots.txt`
- Modify: `docs/site/static/_worker.js`

- [ ] **Step 1: Update posthog.ts — change global variable name**

Replace the entire content of `docs/site/src/clientModules/posthog.ts` with:

```ts
import posthog from 'posthog-js';
import siteConfig from '@generated/docusaurus.config';

const key  = (siteConfig.customFields?.POSTHOG_KEY  as string | undefined) || '';
const host = (siteConfig.customFields?.POSTHOG_HOST as string | undefined) || 'https://us.i.posthog.com';

if (typeof window !== 'undefined' && key) {
  posthog.init(key, {
    api_host: host,
    capture_pageview: true,
    persistence: 'memory',
    autocapture: false,
    disable_session_recording: true,
  });
  (window as any).__cubeplex_posthog = posthog;
}

export {};
```

- [ ] **Step 2: Update DocFeedback to use new global name**

In `docs/site/src/components/DocFeedback/index.tsx`, change the posthog global reference. Replace `(window as any).__cubepi_posthog` with `(window as any).__cubeplex_posthog`.

- [ ] **Step 3: Rewrite robots.txt**

Replace the entire content of `docs/site/static/robots.txt` with:

```
User-agent: *
Allow: /

Sitemap: https://docs.cubeplex.ai/sitemap.xml

User-agent: GPTBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: anthropic-ai
Allow: /
```

- [ ] **Step 4: Update _worker.js for cubeplex domain**

Replace the entire content of `docs/site/static/_worker.js` with:

```js
export default {
  async fetch(request, env) {
    return env.ASSETS.fetch(request);
  },
};
```

- [ ] **Step 5: Commit**

```bash
git add docs/site/src/clientModules/posthog.ts docs/site/src/components/DocFeedback/index.tsx docs/site/static/robots.txt docs/site/static/_worker.js
git commit -m "chore(docs): update PostHog global, robots.txt, worker for CubePlex"
```

---

## Task 9: Install dependencies and verify build

**Files:**
- Create: `docs/site/docs/intro.mdx` (placeholder to unblock build)

- [ ] **Step 1: Create minimal intro.mdx so the build has at least one doc**

Create `docs/site/docs/intro.mdx`:

```mdx
---
slug: /
title: Welcome to CubePlex
---

# Welcome to CubePlex

CubePlex is an AI agent workspace — multi-model conversations, skills, memory, MCP tools, and automation in one platform.
```

- [ ] **Step 2: Install dependencies**

```bash
cd /home/chris/cubeplex/docs/site && pnpm install
```

- [ ] **Step 3: Run build to verify config is valid**

```bash
cd /home/chris/cubeplex/docs/site && pnpm build
```

Expected: build succeeds with no errors.

- [ ] **Step 4: Fix any build errors**

If build fails, fix the errors (likely broken imports from removed components). Common fixes:
- Remove `clientModules` reference from config if posthog import fails without the key
- Remove stale imports in theme overrides

- [ ] **Step 5: Commit**

```bash
git add docs/site/
git commit -m "chore(docs): install deps, verify build passes"
```

---

## Task 10: Write Getting Started docs (3 docs)

**Files:**
- Create: `docs/site/docs/intro.mdx`
- Create: `docs/site/docs/getting-started/quick-start.md`
- Create: `docs/site/docs/getting-started/core-concepts.md`
- Create: `docs/site/docs/getting-started/workspace-setup.md`

Write all 3 getting-started docs plus the intro page. Each doc must have frontmatter with `sidebar_position` for ordering. Content should be written from the end-user perspective, referencing the actual CubePlex UI.

**Intro page** (`intro.mdx`): Product overview — what CubePlex is, who it's for, what you can do with it. Link to quick-start. Use MDX to include the `<Tabs>` component for deployment-mode differences where needed.

**Quick start** (`quick-start.md`): Register/login → create workspace → start first conversation → see agent response. Use `<Tabs groupId="deploy-mode">` for Cloud vs Self-hosted registration differences.

**Core concepts** (`core-concepts.md`): Organization → Workspace → Conversation → Agent → Artifact → Skill → Memory. Short definition + one-sentence explanation for each. Use `<Tabs groupId="deploy-mode">` for org creation differences.

**Workspace setup** (`workspace-setup.md`): Configure default model, invite team members, review workspace settings.

- [ ] **Step 1: Write all 4 docs**

Write each file with complete user-facing content. Use the cubeplex frontend exploration data for accuracy: workspaces are at `/(app)/w/[wsId]`, settings at `/(app)/w/[wsId]/settings`, etc.

- [ ] **Step 2: Verify build**

```bash
cd /home/chris/cubeplex/docs/site && pnpm build
```

- [ ] **Step 3: Commit**

```bash
git add docs/site/docs/intro.mdx docs/site/docs/getting-started/
git commit -m "docs: add Getting Started section (intro, quick-start, core-concepts, workspace-setup)"
```

---

## Task 11: Write Conversations guide docs (4 docs)

**Files:**
- Create: `docs/site/docs/guides/conversations/basics.md`
- Create: `docs/site/docs/guides/conversations/attachments.md`
- Create: `docs/site/docs/guides/conversations/artifacts.md`
- Create: `docs/site/docs/guides/conversations/model-selection.md`

**basics.md**: Starting a conversation, sending messages, multi-turn context, pinning/renaming conversations, conversation list.

**attachments.md**: Supported file types (documents, images, code), how to attach files, how the agent uses attached files.

**artifacts.md**: What artifacts are (agent-generated deliverables), artifact types (file, website, code, document, image, data), previewing, downloading, versioning.

**model-selection.md**: How to switch models per conversation, what models are available (depends on admin config), capability differences between providers.

- [ ] **Step 1: Write all 4 docs**
- [ ] **Step 2: Verify build**

```bash
cd /home/chris/cubeplex/docs/site && pnpm build
```

- [ ] **Step 3: Commit**

```bash
git add docs/site/docs/guides/conversations/
git commit -m "docs: add Conversations guide (basics, attachments, artifacts, model-selection)"
```

---

## Task 12: Write Skills guide docs (3 docs)

**Files:**
- Create: `docs/site/docs/guides/skills/overview.md`
- Create: `docs/site/docs/guides/skills/discover-and-install.md`
- Create: `docs/site/docs/guides/skills/managing-skills.md`

**overview.md**: What skills are, three sources (built-in, uploaded by admin, remote registries), how they extend agent capabilities.

**discover-and-install.md**: Discovering skills from chat ("I need to build a slide deck"), agent searches and shows candidates, one-click install, immediate availability.

**managing-skills.md**: Workspace skills page, enabling/disabling skills, org-level skill management (admin), registry configuration.

- [ ] **Step 1: Write all 3 docs**
- [ ] **Step 2: Verify build**

```bash
cd /home/chris/cubeplex/docs/site && pnpm build
```

- [ ] **Step 3: Commit**

```bash
git add docs/site/docs/guides/skills/
git commit -m "docs: add Skills guide (overview, discover-and-install, managing-skills)"
```

---

## Task 13: Write Memory guide docs (3 docs)

**Files:**
- Create: `docs/site/docs/guides/memory/overview.md`
- Create: `docs/site/docs/guides/memory/using-memory.md`
- Create: `docs/site/docs/guides/memory/managing-memory.md`

**overview.md**: Three-tier memory model — personal (private, cross-workspace), workspace (shared with team), organization (shared across all workspaces). Memory types: preference, project_fact, procedure, correction, decision, org_policy.

**using-memory.md**: How the agent uses memory in conversations (auto-recall of relevant context), how users can correct the agent ("remember that I prefer X"), confidence scoring.

**managing-memory.md**: Memory Center page — view all memory items, filter by scope/type, edit content, archive items, source tracing (which conversation created a memory).

- [ ] **Step 1: Write all 3 docs**
- [ ] **Step 2: Verify build**

```bash
cd /home/chris/cubeplex/docs/site && pnpm build
```

- [ ] **Step 3: Commit**

```bash
git add docs/site/docs/guides/memory/
git commit -m "docs: add Memory guide (overview, using-memory, managing-memory)"
```

---

## Task 14: Write MCP Tools guide docs (3 docs)

**Files:**
- Create: `docs/site/docs/guides/mcp/overview.md`
- Create: `docs/site/docs/guides/mcp/installing-connectors.md`
- Create: `docs/site/docs/guides/mcp/using-tools.md`

**overview.md**: What MCP (Model Context Protocol) connectors are, how they let agents interact with external services, the four-layer model (templates → installs → grants → active).

**installing-connectors.md**: Browsing the connector catalog, installing a connector to your workspace, configuring authentication (API key, OAuth, bearer token).

**using-tools.md**: How the agent uses connected tools in conversation, tool citations (tracing which tool provided information), viewing tool call details.

- [ ] **Step 1: Write all 3 docs**
- [ ] **Step 2: Verify build**

```bash
cd /home/chris/cubeplex/docs/site && pnpm build
```

- [ ] **Step 3: Commit**

```bash
git add docs/site/docs/guides/mcp/
git commit -m "docs: add MCP Tools guide (overview, installing-connectors, using-tools)"
```

---

## Task 15: Write Automation guide docs (2 docs)

**Files:**
- Create: `docs/site/docs/guides/automation/scheduled-tasks.md`
- Create: `docs/site/docs/guides/automation/event-triggers.md`

**scheduled-tasks.md**: Three schedule kinds (cron expression, fixed interval, one-shot), creating a task (name, schedule, prompt, target conversation), reuse vs fresh conversation, pause/resume, missed-run policy, run history.

**event-triggers.md**: What triggers are (inbound webhooks that start agent runs), creating a trigger, webhook URL + HMAC secret, configuring filters (field matchers), rate limiting, event log, example: connecting a GitHub webhook.

- [ ] **Step 1: Write both docs**
- [ ] **Step 2: Verify build**

```bash
cd /home/chris/cubeplex/docs/site && pnpm build
```

- [ ] **Step 3: Commit**

```bash
git add docs/site/docs/guides/automation/
git commit -m "docs: add Automation guide (scheduled-tasks, event-triggers)"
```

---

## Task 16: Write Administration docs (6 docs)

**Files:**
- Create: `docs/site/docs/admin/models.md`
- Create: `docs/site/docs/admin/members.md`
- Create: `docs/site/docs/admin/mcp-connectors.md`
- Create: `docs/site/docs/admin/skills-management.md`
- Create: `docs/site/docs/admin/sandbox.md`
- Create: `docs/site/docs/admin/cost-tracking.md`

**models.md**: Adding a provider (Anthropic, OpenAI, custom endpoint), entering API keys, testing provider liveness, configuring models (reasoning, modalities, costs), using presets.

**members.md**: Inviting members to organization, role hierarchy (owner > admin > member), workspace-level roles, removing members.

**mcp-connectors.md**: Managing the connector catalog (admin view), adding custom connector templates, configuring OAuth for connectors, granting connector access to workspaces.

**skills-management.md**: Uploading skills to the organization, connecting external skill registries (e.g. skills.sh), enabling/disabling skills org-wide, managing skill versions.

**sandbox.md**: What the sandbox is (isolated code execution), configuring sandbox policies (network access, resource limits, egress filtering), managing environment variables and secrets (org/workspace/user scoped).

**cost-tracking.md**: Usage dashboard, per-model cost breakdown, tracking spend over time.

- [ ] **Step 1: Write all 6 docs**
- [ ] **Step 2: Verify build**

```bash
cd /home/chris/cubeplex/docs/site && pnpm build
```

- [ ] **Step 3: Commit**

```bash
git add docs/site/docs/admin/
git commit -m "docs: add Administration section (models, members, mcp-connectors, skills, sandbox, cost-tracking)"
```

---

## Task 17: Final build verification and dev server smoke test

**Files:** None (verification only)

- [ ] **Step 1: Clean build**

```bash
cd /home/chris/cubeplex/docs/site && pnpm clear && pnpm build
```

Expected: build succeeds, no broken links, no warnings.

- [ ] **Step 2: Start dev server and verify in browser**

```bash
cd /home/chris/cubeplex/docs/site && pnpm start -- --host 0.0.0.0
```

Verify in browser:
- Homepage loads with Hero + FeatureGrid (6 cards)
- All 6 FeatureGrid links navigate to correct docs
- Sidebar shows all 7 sections with correct nesting
- All 24 docs render without errors
- Dark mode toggle works
- Locale dropdown shows English + 简体中文 (Chinese locale won't have translated content yet, that's expected)
- GitHub link in navbar works

- [ ] **Step 3: Commit any final fixes**

```bash
git add docs/site/
git commit -m "docs: final build verification and fixes"
```

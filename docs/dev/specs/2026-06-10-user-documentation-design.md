# CubePlex User Documentation Site — Design Spec

**Date**: 2026-06-10
**Status**: Approved

---

## Goal

Create a standalone documentation site for CubePlex end-users (both self-hosted
and SaaS), covering the full product from onboarding to administration.

## Decisions

| Decision | Outcome |
|---|---|
| Target audience | Self-hosted + SaaS users; deployment-mode differences marked inline |
| Framework | Docusaurus 3.10, forked from cubepi `website/` |
| Location | `docs/site/` within the cubeplex repo |
| Language | English primary; i18n skeleton for `zh-Hans` (translation is a follow-up) |
| First-version scope | 24 docs across 7 sections |
| Homepage | Hero + FeatureGrid (6 tiles) |
| Versioning | Off for now; enable after v1 release |
| Analytics | PostHog + GA slots present, tracking IDs left empty |
| Deployment-mode handling | `<Tabs>` / Admonitions at the paragraph level; no full-page branching |

## Source: cubepi `website/` fork

### What to keep

- `docusaurus.config.ts` structure (rewrite brand, URL, tagline; keep i18n
  `en` + `zh-Hans`; remove version config)
- `src/css/custom.css` design system (tokens, Inter / JetBrains Mono fonts);
  update brand color
- `src/components/Home/Hero.tsx` and `FeatureGrid.tsx` — replace content
- `static/fonts/` — same fonts
- Cloudflare Pages `_worker.js` — keep for future deployment
- `tsconfig.json`, `vitest.config.ts` — keep as-is

### What to remove

- `versioned_docs/`, `versioned_sidebars/`, `versions.json`
- `src/pages/compare/` (competitive comparison pages)
- `src/pages/faq.tsx`
- `scripts/build_api_reference.py` and `docs/api/` (Python API doc generator)
- `src/components/Home/WhyTable.tsx`, `HelloAgent.tsx`, `InstallMatrix.tsx`,
  `MetaBar.tsx` and their CSS modules
- `src/components/Compare/`
- `src/components/VersionAwareDocLink.tsx` and related config/tests
- All cubepi doc content (`docs/` markdown files)

## Documentation structure

```
docs/
├── intro.mdx                          # Product intro — what is CubePlex, capability overview
├── getting-started/
│   ├── quick-start.md                 # Register/login → create workspace → first conversation
│   ├── core-concepts.md               # Key concepts: Organization, Workspace, Agent, Conversation, Artifact
│   └── workspace-setup.md             # Workspace config: model selection, invite members
├── guides/
│   ├── conversations/
│   │   ├── basics.md                  # Sending messages, context, multi-turn
│   │   ├── attachments.md             # File attachments (documents, images, code)
│   │   ├── artifacts.md               # Agent-generated artifacts: preview, download, versions
│   │   └── model-selection.md         # Switching models, capability differences
│   ├── skills/
│   │   ├── overview.md                # What is a Skill; three sources (built-in / uploaded / remote)
│   │   ├── discover-and-install.md    # Discover and install skills from chat
│   │   └── managing-skills.md         # Workspace / org-level skill management
│   ├── memory/
│   │   ├── overview.md                # Three-tier memory: personal / workspace / organization
│   │   ├── using-memory.md            # How the agent uses memory; how users correct it
│   │   └── managing-memory.md         # Memory Center: view, edit, archive
│   ├── mcp/
│   │   ├── overview.md                # What are MCP connectors, tool integration concept
│   │   ├── installing-connectors.md   # Install from Catalog, configure auth
│   │   └── using-tools.md             # Using tools in conversation, citation tracing
│   └── automation/
│       ├── scheduled-tasks.md         # Cron / interval / one-shot tasks
│       └── event-triggers.md          # Webhook triggers: create, configure, event log
├── admin/
│   ├── models.md                      # Provider management, model config, presets
│   ├── members.md                     # Org members, roles, invitations
│   ├── mcp-connectors.md             # Catalog management, OAuth config
│   ├── skills-management.md           # Skill upload, registry config
│   ├── sandbox.md                     # Sandbox policies, environment variables
│   └── cost-tracking.md              # Usage and cost tracking
```

**Total: 24 documents.**

## Sidebar config

```ts
const sidebars = {
  docs: [
    'intro',
    {
      type: 'category',
      label: 'Getting Started',
      collapsed: false,
      items: ['getting-started/quick-start', 'getting-started/core-concepts', 'getting-started/workspace-setup'],
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
```

## Homepage

### Hero

- **Title**: CubePlex
- **Tagline**: "Your AI agent workspace — chat, automate, extend"
- **CTA buttons**: Get Started → `/getting-started/quick-start`, GitHub → repo URL

### FeatureGrid (6 tiles)

| Icon | Title | Description |
|---|---|---|
| Chat | Conversations | Multi-model chat with file attachments and artifact generation |
| Puzzle | Skills | Discover and install agent capabilities in one click |
| Brain | Memory | Three-tier memory — the agent learns as you work |
| Plug | MCP Tools | Connect external services; the agent calls APIs for you |
| Clock | Automation | Scheduled tasks + webhook triggers for hands-free operation |
| Shield | Admin | Model management, team roles, cost tracking, sandbox policies |

## Deployment-mode handling

Where the UX differs between single-tenant (self-hosted) and multi-tenant
(cloud), use Docusaurus `<Tabs>` at the paragraph level:

```mdx
<Tabs groupId="deploy-mode">
  <TabItem value="cloud" label="Cloud">
    Sign up at cubeplex.ai. Your organization is created automatically.
  </TabItem>
  <TabItem value="self-hosted" label="Self-hosted">
    The first user to register creates the organization and becomes its owner.
  </TabItem>
</Tabs>
```

Affected pages (known): `quick-start.md`, `core-concepts.md`. Most guides
and admin pages are identical across modes.

## i18n

- Default locale: `en`
- Secondary locale: `zh-Hans`
- First version ships English only; `i18n/zh-Hans/` skeleton generated via
  `docusaurus write-translations`
- Chinese doc translation is a separate follow-up task

## Dev workflow

```bash
cd docs/site
pnpm install
pnpm start              # dev server
pnpm build              # production build
pnpm start -- --locale zh-Hans  # Chinese preview
```

`docs/site/` is self-contained with its own `package.json` and
`node_modules`. It is not part of the frontend pnpm workspace.

## Out of scope (first version)

- Self-hosting / deployment guide (handled in a separate session)
- Version management (enable after v1)
- Chinese translation (follow-up task)
- CI/CD pipeline for the docs site
- API reference
- FAQ page
- Competitive comparison pages
- Search (Algolia / local) — add later

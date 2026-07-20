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
            'guides/conversations/topics',
            'guides/conversations/sandboxes',
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
            'guides/mcp/enabling-connectors',
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
        {
          type: 'category',
          label: 'IM Connectors',
          items: [
            'guides/im/overview',
            'guides/im/feishu',
            'guides/im/slack',
            'guides/im/dingtalk',
            'guides/im/teams',
            'guides/im/discord',
          ],
        },
        {
          type: 'category',
          label: 'Account',
          items: ['guides/account/profile'],
        },
      ],
    },
    {
      type: 'category',
      label: 'Deployment',
      items: [
        'deployment/overview',
        'deployment/docker-compose',
        'deployment/kubernetes',
        'deployment/backend-config',
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

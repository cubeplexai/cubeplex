import type { SidebarsConfig } from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docs: [
    'intro',
    {
      type: 'category',
      label: 'Getting Started',
      collapsed: false,
      items: [
        'getting-started/installation',
        'getting-started/quick-start',
        'getting-started/core-concepts',
      ],
    },
    {
      type: 'category',
      label: 'Guides',
      items: [
        { type: 'category', label: 'Agents', items: [
          'guides/agents/first-agent',
          'guides/agents/tool-use',
          'guides/agents/multi-turn',
          'guides/agents/streaming',
          'guides/agents/forking',
          'guides/agents/prompt-caching',
        ]},
        { type: 'category', label: 'Providers', items: [
          'guides/providers/overview',
          'guides/providers/anthropic',
          'guides/providers/openai',
          'guides/providers/structured-output',
          'guides/providers/custom',
          'guides/providers/image-generation',
        ]},
        { type: 'category', label: 'Checkpointing', items: [
          'guides/checkpointing/sqlite',
          'guides/checkpointing/postgres',
          'guides/checkpointing/mysql',
          'guides/checkpointing/custom',
        ]},
        { type: 'category', label: 'Middleware', items: [
          'guides/middleware/hooks',
          'guides/middleware/composition',
          'guides/middleware/examples',
          'guides/middleware/compaction',
          'guides/middleware/subagents',
          'guides/middleware/todo',
          'guides/middleware/deferred-tools',
          'guides/middleware/goal',
        ]},
        { type: 'category', label: 'Human-in-the-Loop', items: [
          'guides/hitl/overview',
          'guides/hitl/middlewares',
          'guides/hitl/durable',
          'guides/hitl/reference',
        ]},
        { type: 'category', label: 'MCP', items: [
          'guides/mcp/loading',
          'guides/mcp/auth',
        ]},
        { type: 'category', label: 'Tracing', items: [
          'guides/tracing/overview',
          'guides/tracing/getting-started',
          'guides/tracing/cli',
          'guides/tracing/otlp',
          'guides/tracing/content-recording',
          'guides/tracing/metrics',
        ]},
      ],
    },
    {
      type: 'category',
      label: 'API Reference',
      link: { type: 'doc', id: 'api/index' },
      items: [
        'api/cubepi-agent',
        'api/cubepi-providers',
        'api/cubepi-checkpointer',
        'api/cubepi-middleware',
        'api/cubepi-mcp',
        'api/cubepi-tracing',
        'api/cubepi-utils',
      ],
    },
    {
      type: 'category',
      label: 'Recipes',
      items: [
        'recipes/weather-agent',
        'recipes/multi-provider-failover',
        'recipes/persistent-chat',
        'recipes/resumable-tasks',
        'recipes/postgres-fastapi',
        'recipes/ask-user-form',
        'recipes/sandbox-confirm',
      ],
    },
    {
      type: 'category',
      label: 'Migration',
      items: ['migration/from-langgraph'],
    },
  ],
};

export default sidebars;

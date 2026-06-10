import React from 'react';
import Link from '@docusaurus/Link';
import { useIsZhHans } from '@site/src/hooks/useIsZhHans';
import styles from './FeatureGrid.module.css';

type Icon = React.FC<React.SVGProps<SVGSVGElement>>;

const IconBox: Icon = (p) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <rect x="2" y="2" width="12" height="12" rx="2" />
  </svg>
);
const IconStream: Icon = (p) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M1 4h14M1 8h14M1 12h10" />
  </svg>
);
const IconTool: Icon = (p) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M11 2l3 3-2 2-3-3zM10 5l-7 7v3h3l7-7" />
  </svg>
);
const IconPlug: Icon = (p) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M5 1v4M11 1v4M3 5h10v4a4 4 0 01-4 4H7a4 4 0 01-4-4V5zM8 13v2" />
  </svg>
);
const IconDisk: Icon = (p) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <ellipse cx="8" cy="4" rx="6" ry="2" />
    <path d="M2 4v8c0 1.1 2.7 2 6 2s6-.9 6-2V4M2 8c0 1.1 2.7 2 6 2s6-.9 6-2" />
  </svg>
);
const IconMcp: Icon = (p) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <circle cx="8" cy="8" r="6" />
    <path d="M2 8h12M8 2v12" />
  </svg>
);
const IconTrace: Icon = (p) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <path d="M2 3h9M5 7h9M3 11h11" />
    <circle cx="2" cy="3" r="1" fill="currentColor" />
    <circle cx="5" cy="7" r="1" fill="currentColor" />
    <circle cx="3" cy="11" r="1" fill="currentColor" />
  </svg>
);

const IconHitl: Icon = (p) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" {...p}>
    <circle cx="6" cy="5" r="2.5" />
    <path d="M2 13.5a4 4 0 017.2-2.4" />
    <path d="M10 9.5l1.6 1.6L15 7.7" />
  </svg>
);

const CARDS_EN = [
  { Icon: IconBox,    title: 'Agents',             body: 'One async loop, fully typed events.',          href: '/docs/guides/agents/first-agent' },
  { Icon: IconStream, title: 'Streaming',           body: 'async for event in stream.',                  href: '/docs/guides/agents/streaming' },
  { Icon: IconTool,   title: 'Tools',               body: 'Plain functions, parallel execution.',        href: '/docs/guides/agents/tool-use' },
  { Icon: IconPlug,   title: 'Providers',           body: 'Anthropic, OpenAI, or write your own.',       href: '/docs/guides/providers/overview' },
  { Icon: IconDisk,   title: 'Checkpointing',       body: 'Append-only, O(1) per turn.',                href: '/docs/guides/checkpointing/sqlite' },
  { Icon: IconHitl,   title: 'Human-in-the-loop',  body: 'Pause for confirm, approve, or ask.',         href: '/docs/guides/hitl/overview' },
  { Icon: IconMcp,    title: 'MCP',                 body: 'Load remote tools at startup.',               href: '/docs/guides/mcp/loading' },
  { Icon: IconTrace,  title: 'Tracing',             body: 'OpenTelemetry, OTLP / JSONL, GenAI semconv.', href: '/docs/guides/tracing/overview' },
];

const CARDS_ZH = [
  { Icon: IconBox,    title: 'Agent',          body: '一个 async 循环，完整类型化事件。',             href: '/docs/guides/agents/first-agent' },
  { Icon: IconStream, title: '流式输出',        body: 'async for event in stream。',                 href: '/docs/guides/agents/streaming' },
  { Icon: IconTool,   title: '工具调用',        body: '普通函数，并行执行。',                         href: '/docs/guides/agents/tool-use' },
  { Icon: IconPlug,   title: 'Provider',       body: 'Anthropic、OpenAI，或自定义。',               href: '/docs/guides/providers/overview' },
  { Icon: IconDisk,   title: '检查点',          body: '追加式，每轮 O(1)。',                         href: '/docs/guides/checkpointing/sqlite' },
  { Icon: IconHitl,   title: '人机协同',        body: '暂停等待确认、审批或问答。',                   href: '/docs/guides/hitl/overview' },
  { Icon: IconMcp,    title: 'MCP',            body: '启动时加载远程工具。',                         href: '/docs/guides/mcp/loading' },
  { Icon: IconTrace,  title: 'Tracing',        body: 'OpenTelemetry、OTLP / JSONL、GenAI semconv。', href: '/docs/guides/tracing/overview' },
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
            <span className={styles.more}>→ {zh ? '指南 / ' : 'Guides / '}{c.title}</span>
          </Link>
        ))}
      </div>
    </section>
  );
}

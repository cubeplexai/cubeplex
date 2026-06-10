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

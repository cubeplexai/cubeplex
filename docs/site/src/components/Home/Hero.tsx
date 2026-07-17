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

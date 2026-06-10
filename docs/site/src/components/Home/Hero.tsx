import React from 'react';
import Link from '@docusaurus/Link';
import useBaseUrl from '@docusaurus/useBaseUrl';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import { useIsZhHans } from '@site/src/hooks/useIsZhHans';
import styles from './Hero.module.css';

export default function Hero() {
  const social = useBaseUrl('/img/brand/cubepi-social-preview.png');
  const installCmd = 'pip install cubepi';
  const { siteConfig } = useDocusaurusContext();
  const zh = useIsZhHans();
  // Sourced from siteConfig.customFields.PACKAGE_VERSION (parsed from
  // pyproject.toml at config-load time) so the eyebrow tracks the
  // released version automatically.
  const version =
    (siteConfig.customFields?.PACKAGE_VERSION as string | undefined) ?? 'dev';

  return (
    <section className={styles.hero}>
      <img
        className={styles.banner}
        src={social}
        alt="CubePi · A Pythonic, async-native agent framework"
        width={1280}
        height={640}
        fetchPriority="high"
      />
      <div className={styles.eyebrow}>cubepi · v{version}</div>
      {/* Tagline lives inside the <h1> (as a styled subline) so the page's
          single H1 carries the descriptive keyword phrase, not just the brand
          name — while rendering identically to the previous brand + tagline
          stack. */}
      <h1 className={styles.h1}>
        CubePi
        <span className={styles.h1sub}>
          {zh ? '一个 Pythonic 原生异步 Agent 框架。' : 'A Pythonic, async-native agent framework.'}
        </span>
      </h1>
      <p className={styles.lead}>
        {zh ? (
          <>
            CubePi 是一个 Pythonic 原生异步 Agent 框架，专为高性能、高可读性和生产级持久化而设计。
            它以线性 <code>while</code> 循环建模 agent 逻辑，提供比图结构 agent 运行时更轻量的替代方案，
            开发者可以轻松追踪和调试。
          </>
        ) : (
          <>
            CubePi is a Pythonic, async-native agent framework designed for high
            performance, readability, and production-grade persistence. It provides
            a leaner alternative to graph-based agent runtimes by modeling agent
            logic as a linear <code>while</code> loop that developers can easily
            trace and debug.
          </>
        )}
      </p>
      <div className={styles.actions}>
        <button
          type="button"
          className={`${styles.cta} ${styles.ctaPrimary}`}
          onClick={() => navigator.clipboard?.writeText(installCmd)}
          aria-label={`Copy install command: ${installCmd}`}
        >
          <code>{installCmd}</code>
          <kbd className={styles.kbd}>⌘C</kbd>
        </button>
        <Link className={`${styles.cta} ${styles.ctaGhost}`} to="/docs/getting-started/quick-start">
          {zh ? '快速开始 →' : 'Quick Start →'}
          <kbd className={styles.kbd}>G Q</kbd>
        </Link>
      </div>
    </section>
  );
}

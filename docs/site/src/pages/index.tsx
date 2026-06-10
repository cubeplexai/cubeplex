import React from 'react';
import Head from '@docusaurus/Head';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import Layout from '@theme/Layout';
import Hero from '@site/src/components/Home/Hero';
import WhyTable from '@site/src/components/Home/WhyTable';
import HelloAgent from '@site/src/components/Home/HelloAgent';
import FeatureGrid from '@site/src/components/Home/FeatureGrid';
import InstallMatrix from '@site/src/components/Home/InstallMatrix';
import MetaBar from '@site/src/components/Home/MetaBar';
import { useIsZhHans } from '@site/src/hooks/useIsZhHans';

export default function Home(): React.ReactElement {
  const zh = useIsZhHans();
  const { siteConfig } = useDocusaurusContext();
  const version =
    (siteConfig.customFields?.PACKAGE_VERSION as string | undefined) ?? 'dev';

  // Product-level structured data lives on the homepage only (the global
  // headTags carry the site-wide Organization). Enriched with offers (free),
  // the released version, and links the org `sameAs` points at, so Google can
  // surface a richer software result.
  const softwareJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'SoftwareApplication',
    name: 'CubePi',
    description:
      'A Pythonic, async-native agent framework — a leaner alternative to langgraph and pi-agent-core. Plain async functions, append-only checkpointing, minimal dependencies.',
    url: 'https://cubepi.ai',
    applicationCategory: 'DeveloperApplication',
    operatingSystem: 'Linux, macOS, Windows',
    programmingLanguage: 'Python',
    softwareVersion: version,
    downloadUrl: 'https://pypi.org/project/cubepi/',
    softwareHelp: 'https://cubepi.ai/docs/',
    license: 'https://github.com/cubeplexai/cubepi/blob/main/LICENSE',
    offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
    author: { '@type': 'Organization', name: 'CubePi', url: 'https://cubepi.ai' },
  };

  return (
    <Layout
      // The site title ("CubePi") is appended via the title delimiter, so the
      // page title here must NOT lead with the brand or it double-brands the
      // <title>/og:title (e.g. "CubePi — … | CubePi").
      title={zh
        ? 'Pythonic 异步原生 Agent 框架'
        : 'A Pythonic, async-native agent framework'}
      description={zh
        ? 'CubePi 是 langgraph 和 pi-agent-core 的 Pythonic 异步原生替代方案。普通 async 函数、追加式持久化、3 个核心依赖。'
        : 'CubePi is a Pythonic async-native agent framework — a leaner alternative to langgraph and pi-agent-core. Plain async functions, append-only checkpointing, 3 core dependencies.'}
    >
      <Head>
        <script type="application/ld+json">{JSON.stringify(softwareJsonLd)}</script>
      </Head>
      <Hero />
      <WhyTable />
      <HelloAgent />
      <FeatureGrid />
      <InstallMatrix />
      <MetaBar />
    </Layout>
  );
}

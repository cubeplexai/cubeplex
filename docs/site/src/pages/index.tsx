import React from 'react';
import Head from '@docusaurus/Head';
import Layout from '@theme/Layout';
import Hero from '@site/src/components/Home/Hero';
import FeatureGrid from '@site/src/components/Home/FeatureGrid';

export default function Home(): React.ReactElement {
  const softwareJsonLd = {
    '@context': 'https://schema.org',
    '@type': 'SoftwareApplication',
    name: 'CubeBox',
    description: 'Your AI agent workspace — chat, automate, extend.',
    url: 'https://cubebox.ai',
    applicationCategory: 'BusinessApplication',
    operatingSystem: 'Web',
    offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
    author: { '@type': 'Organization', name: 'CubeBox', url: 'https://cubebox.ai' },
  };

  return (
    <Layout
      title="Your AI agent workspace"
      description="CubeBox is an AI agent workspace — multi-model chat, skills, memory, MCP tools, and automation in one platform."
    >
      <Head>
        <script type="application/ld+json">{JSON.stringify(softwareJsonLd)}</script>
      </Head>
      <Hero />
      <FeatureGrid />
    </Layout>
  );
}

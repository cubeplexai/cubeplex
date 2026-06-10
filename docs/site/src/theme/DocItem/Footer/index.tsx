import React from 'react';
import Footer from '@theme-original/DocItem/Footer';
import { useDoc } from '@docusaurus/plugin-content-docs/client';
import { useLocation } from '@docusaurus/router';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import DocFeedback from '@site/src/components/DocFeedback';

export default function FooterWrapper(props: any): React.ReactElement {
  const { metadata } = useDoc();
  const { i18n } = useDocusaurusContext();
  const { pathname } = useLocation();
  const version = (metadata as any).version ?? 'current';
  return (
    <>
      <DocFeedback slug={pathname} version={version} locale={i18n.currentLocale} />
      <Footer {...props} />
    </>
  );
}

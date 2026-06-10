import React from 'react';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';

/**
 * Inline component that renders the current cubepi package version,
 * sourced from `siteConfig.customFields.PACKAGE_VERSION` which
 * `docusaurus.config.ts` parses out of `pyproject.toml` at
 * config-load time.
 *
 * Use in MDX:
 *
 *     import PackageVersion from '@site/src/components/PackageVersion';
 *     The latest released version is `v<PackageVersion />`.
 *
 * Falls back to `dev` if the custom field is missing (e.g. the
 * pyproject.toml read failed during config load).
 */
export default function PackageVersion(): React.ReactElement {
  const { siteConfig } = useDocusaurusContext();
  const version =
    (siteConfig.customFields?.PACKAGE_VERSION as string | undefined) ?? 'dev';
  return <>{version}</>;
}

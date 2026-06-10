import posthog from 'posthog-js';
import siteConfig from '@generated/docusaurus.config';

// Use `||` not `??`: the build embeds POSTHOG_HOST="" when the secret is
// unset, and we need the empty string to fall back to the US endpoint.
const key  = (siteConfig.customFields?.POSTHOG_KEY  as string | undefined) || '';
const host = (siteConfig.customFields?.POSTHOG_HOST as string | undefined) || 'https://us.i.posthog.com';

if (typeof window !== 'undefined' && key) {
  posthog.init(key, {
    api_host: host,
    capture_pageview: true,
    persistence: 'memory',
    autocapture: false,
    disable_session_recording: true,
  });
  (window as any).__cubepi_posthog = posthog;
}

export {};

import posthog from 'posthog-js';
import siteConfig from '@generated/docusaurus.config';

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
  (window as any).__cubebox_posthog = posthog;
}

export {};

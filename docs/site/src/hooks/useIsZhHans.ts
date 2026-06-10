import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
export function useIsZhHans(): boolean {
  const { i18n } = useDocusaurusContext();
  return i18n.currentLocale === 'zh-Hans';
}

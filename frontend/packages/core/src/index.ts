export * from './types'
export * from './api'
export * from './stores'
export { bareToolName } from './lib/toolName'
export {
  artifactBasename,
  isMarkdownArtifact,
  isMarkdownEditable,
  markdownFilename,
} from './lib/artifactMarkdown'
export type * from './types/provider'
export * from './api/providers'
export { useProvidersStore } from './stores/providersStore'
export { useModelsStore } from './stores/modelsStore'
export * from './oauth'
export * from './auth'
export { useOrgAdminFlag } from './hooks/useOrgAdminFlag'

// `useDeploymentMode` and `useUserEvents` are intentionally NOT re-exported
// from this barrel. They are `'use client'` hooks that pull react / swr;
// re-exporting them here would force every server/proxy file that imports
// anything from `@cubeplex/core` (e.g. `app/page.tsx`, `proxy.ts`, both
// server contexts that only need `AUTH_COOKIE_NAME`) to drag react/swr
// into the bundle. Client-side consumers should import directly from
// `@cubeplex/core/hooks/useDeploymentMode` or
// `@cubeplex/core/hooks/useUserEvents` (or a dedicated client subpath
// barrel once one exists).

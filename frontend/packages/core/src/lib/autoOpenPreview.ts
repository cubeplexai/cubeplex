/**
 * Preferences that decide whether a preview panel opens automatically when
 * the corresponding work finishes (e.g. an artifact is saved mid-stream).
 *
 * Browser-local only for now — no server-backed user settings. Other panel
 * types (write_file, file_read, sandbox, …) can share this module later.
 */

import { bareToolName } from './toolName'

export const AUTO_OPEN_ARTIFACTS_STORAGE_KEY = 'cubeplex.preview.autoOpen.artifacts'

/** Default when the key is unset: auto-open artifacts after save. */
export const DEFAULT_AUTO_OPEN_ARTIFACTS = true

/**
 * Parse a storage value. Explicit `'false'` / `'0'` disable; anything else
 * (including missing) follows the default-on policy.
 */
export function parseAutoOpenArtifacts(raw: string | null | undefined): boolean {
  if (raw === null || raw === undefined) return DEFAULT_AUTO_OPEN_ARTIFACTS
  if (raw === 'false' || raw === '0') return false
  if (raw === 'true' || raw === '1') return true
  return DEFAULT_AUTO_OPEN_ARTIFACTS
}

export function isAutoOpenArtifactsEnabled(): boolean {
  if (typeof localStorage === 'undefined') return DEFAULT_AUTO_OPEN_ARTIFACTS
  try {
    return parseAutoOpenArtifacts(localStorage.getItem(AUTO_OPEN_ARTIFACTS_STORAGE_KEY))
  } catch {
    return DEFAULT_AUTO_OPEN_ARTIFACTS
  }
}

export function setAutoOpenArtifactsEnabled(enabled: boolean): void {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(AUTO_OPEN_ARTIFACTS_STORAGE_KEY, enabled ? 'true' : 'false')
  } catch {
    // quota / private mode — preference is best-effort
  }
}

/**
 * Whether a live artifact event should open the preview panel.
 *
 * - Preference must be on.
 * - Conversation must be the mounted chat surface (`viewingConversationId`),
 *   not merely sidebar `activeId` (which can linger on home / draft flows).
 */
export function shouldAutoOpenArtifactPreview(
  conversationId: string,
  viewingConversationId: string | null,
  enabled: boolean = isAutoOpenArtifactsEnabled(),
): boolean {
  return enabled && viewingConversationId === conversationId
}

/**
 * Whether auto-open may replace the current panel view.
 *
 * - Closed → open.
 * - Same artifact id → refresh (version bump).
 * - Another artifact for the same conversation only if the current view was
 *   itself auto-opened (follow the latest deliverable). User-picked artifacts
 *   are never clobbered by a different artifact.
 * - Any other surface (tool, sandbox, attachment, other conversation) → leave
 *   the user's choice alone.
 */
export function canAutoOpenReplacePanel(
  view: {
    type: string
    conversationId?: string
    artifactId?: string
    source?: 'user' | 'auto'
  },
  conversationId: string,
  artifactId: string,
): boolean {
  if (view.type === 'closed') return true
  if (view.type !== 'artifact' || view.conversationId !== conversationId) return false
  if (view.artifactId === artifactId) return true
  return view.source === 'auto'
}

/** True when this tool call means the agent is driving the sandbox browser. */
export function isBrowserToolCall(name: string, args: Record<string, unknown>): boolean {
  const bare = bareToolName(name)
  if (bare === 'load_skill') {
    const skill = String(args.skill_name ?? args.name ?? '')
      .trim()
      .toLowerCase()
    return skill === 'browser' || /(?::|\/|__)browser$/.test(skill)
  }
  if (bare === 'execute') {
    const cmd = String(args.cmd ?? args.command ?? '')
    return /\bagent-browser\b/.test(cmd) || /start-browser\.sh\b/.test(cmd)
  }
  return false
}

/**
 * Whether a live browser tool call should open the sandbox browser preview.
 * Requires the mounted chat surface — same gate as artifact auto-open.
 */
export function shouldAutoOpenBrowserPreview(
  conversationId: string,
  viewingConversationId: string | null,
): boolean {
  return viewingConversationId === conversationId
}

/**
 * Whether browser auto-open may replace the current panel view.
 *
 * - Closed → open.
 * - Already on sandbox (any tab) → switch to the browser tab.
 * - Auto-opened artifact → follow the live browser instead.
 * - User-picked artifact / tool / attachment → leave the user's choice alone.
 */
export function canAutoOpenBrowserPanel(view: { type: string; source?: 'user' | 'auto' }): boolean {
  if (view.type === 'closed' || view.type === 'sandbox') return true
  return view.type === 'artifact' && view.source === 'auto'
}

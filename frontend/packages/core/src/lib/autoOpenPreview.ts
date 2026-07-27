/**
 * Preferences that decide whether a preview panel opens automatically when
 * the corresponding work finishes (e.g. an artifact is saved mid-stream).
 *
 * Browser-local only for now — no server-backed user settings. Other panel
 * types (write_file, file_read, sandbox, …) can share this module later.
 */

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
 * - Already showing an artifact for the same conversation → switch / refresh.
 * - Any other surface (tool, sandbox, attachment, other conversation's
 *   artifact) → leave the user's choice alone.
 */
export function canAutoOpenReplacePanel(
  view: { type: string; conversationId?: string },
  conversationId: string,
): boolean {
  if (view.type === 'closed') return true
  if (view.type === 'artifact' && view.conversationId === conversationId) return true
  return false
}

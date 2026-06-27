/**
 * Response shape for `GET /api/v1/ws/{ws}/sandboxes` — the caller's own
 * sandbox entities in a workspace, as shown on the user-facing sandbox
 * settings panel. Provider-internal fields live on the admin surface.
 */
export interface MySandboxOut {
  id: string
  scope_type: string
  scope_id: string
  scope_title: string | null
  status: string
  image: string
  last_activity_at: string | null
  created_at: string
}

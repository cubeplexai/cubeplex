'use client'

import { useAuthStore } from '../stores/authStore'
import type { OrgMembership } from '../api/auth'

const EMPTY_ORG_MEMBERSHIPS: OrgMembership[] = []

/**
 * Returns true iff the current user is an admin or owner of the given org.
 *
 * Reads from `useAuthStore().user.org_memberships`. Returns false when the
 * org id is not known, when the store has no user, or when no matching
 * membership row is present.
 */
export function useOrgAdminFlag(orgId: string | null | undefined): boolean {
  const memberships = useAuthStore((s) => s.user?.org_memberships ?? EMPTY_ORG_MEMBERSHIPS)
  if (!orgId) return false
  return memberships.some((m) => m.org_id === orgId && (m.role === 'admin' || m.role === 'owner'))
}

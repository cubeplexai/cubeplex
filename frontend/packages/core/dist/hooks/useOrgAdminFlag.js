'use client';
import { useAuthStore } from '../stores/authStore';
/**
 * Returns true iff the current user is an admin or owner of the given org.
 *
 * Reads from `useAuthStore().user.org_memberships`. Returns false when the
 * org id is not known, when the store has no user, or when no matching
 * membership row is present.
 */
export function useOrgAdminFlag(orgId) {
    const memberships = useAuthStore((s) => s.user?.org_memberships ?? []);
    if (!orgId)
        return false;
    return memberships.some((m) => m.org_id === orgId && (m.role === 'admin' || m.role === 'owner'));
}
//# sourceMappingURL=useOrgAdminFlag.js.map
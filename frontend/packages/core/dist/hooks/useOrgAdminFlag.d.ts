/**
 * Returns true iff the current user is an admin or owner of the given org.
 *
 * Reads from `useAuthStore().user.org_memberships`. Returns false when the
 * org id is not known, when the store has no user, or when no matching
 * membership row is present.
 */
export declare function useOrgAdminFlag(orgId: string | null | undefined): boolean;
//# sourceMappingURL=useOrgAdminFlag.d.ts.map
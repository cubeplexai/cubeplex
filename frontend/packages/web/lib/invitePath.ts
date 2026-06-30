export function isInviteAcceptPath(path: string): boolean {
  return path.startsWith('/invite') || path.startsWith('/orgs/invites/accept')
}

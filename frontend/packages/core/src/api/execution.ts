export interface AccessRemovalResult {
  removed: boolean
  cleanup_pending: boolean
}

export interface LeaveWorkspaceResult {
  left: boolean
  cleanup_pending: boolean
}

export interface HardDeleteResult {
  deleted: boolean
  cleanup_pending: boolean
}

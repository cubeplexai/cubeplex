import type { PlatformDescriptor } from './types'

export const teamsDescriptor: PlatformDescriptor = {
  id: 'teams',
  labelKey: 'im.platform.teams.label',
  iconName: 'MessageSquare',
  live: false,
  prereqs: [],
  credentialFields: [],
  steps: [],
  buildPayload: () => {
    throw new Error('Teams is not yet supported')
  },
  scopeConsoleUrl: () => 'https://dev.teams.microsoft.com/',
}

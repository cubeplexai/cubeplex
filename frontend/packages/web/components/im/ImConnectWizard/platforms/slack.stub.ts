import type { PlatformDescriptor } from './types'

export const slackDescriptor: PlatformDescriptor = {
  id: 'slack',
  labelKey: 'im.platform.slack.label',
  iconName: 'Slack',
  live: false,
  prereqs: [],
  credentialFields: [],
  steps: [],
  buildPayload: () => {
    throw new Error('Slack is not yet supported')
  },
  scopeConsoleUrl: () => 'https://api.slack.com/apps',
}

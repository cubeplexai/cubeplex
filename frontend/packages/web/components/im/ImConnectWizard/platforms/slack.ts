import { StepCredentials } from '../steps/StepCredentials'
import { StepPrereqs } from '../steps/StepPrereqs'
import { StepVerify } from '../steps/StepVerify'

import type { PlatformDescriptor } from './types'

export const slackDescriptor: PlatformDescriptor = {
  id: 'slack',
  labelKey: 'im.platform.slack.label',
  iconName: 'Slack',
  live: true,
  prereqs: [
    {
      key: 'app',
      labelKey: 'im.wizard.slack.prereq.app',
      helpUrl: () => 'https://api.slack.com/apps',
    },
    {
      key: 'scopes',
      labelKey: 'im.wizard.slack.prereq.scopes',
      items: [
        'app_mentions:read',
        'chat:write',
        'channels:history',
        'im:history',
        'im:read',
        // Private file download (url_private_download) requires files:read;
        // without it Slack returns a 200 HTML sign-in page and inbound drops.
        'files:read',
        // Outbound agent artifacts → files_upload_v2.
        'files:write',
        'reactions:read',
        'reactions:write',
        'users:read',
        'users:read.email',
        'commands',
      ],
    },
    {
      key: 'socketMode',
      labelKey: 'im.wizard.slack.prereq.socketMode',
    },
    {
      key: 'appToken',
      labelKey: 'im.wizard.slack.prereq.appToken',
      items: ['connections:write'],
    },
    {
      // Socket Mode only changes delivery transport — without these bot
      // event subscriptions Slack never pushes mentions/DMs over the socket.
      key: 'events',
      labelKey: 'im.wizard.slack.prereq.events',
      items: ['app_mention', 'message.im'],
    },
    {
      key: 'install',
      labelKey: 'im.wizard.slack.prereq.install',
    },
  ],
  credentialFields: [
    {
      key: 'bot_token',
      labelKey: 'im.wizard.slack.field.botToken',
      type: 'password',
      required: true,
      placeholder: 'xoxb-...',
    },
    {
      key: 'app_token',
      labelKey: 'im.wizard.slack.field.appToken',
      type: 'password',
      required: true,
      placeholder: 'xapp-...',
    },
  ],
  steps: [
    {
      key: 'prereqs',
      labelKey: 'im.wizard.step.prereqs',
      Component: StepPrereqs,
      canAdvance: () => true,
    },
    {
      key: 'credentials',
      labelKey: 'im.wizard.step.credentials',
      Component: StepCredentials,
      canAdvance: (f) => !!(f.bot_token && f.app_token),
    },
    {
      key: 'verify',
      labelKey: 'im.wizard.step.verify',
      Component: StepVerify,
    },
  ],
  buildPayload: (f) => ({
    platform: 'slack' as const,
    bot_token: f.bot_token || '',
    app_token: f.app_token || '',
    acting_user_id: 'self',
  }),
  scopeConsoleUrl: () => 'https://api.slack.com/apps',
}

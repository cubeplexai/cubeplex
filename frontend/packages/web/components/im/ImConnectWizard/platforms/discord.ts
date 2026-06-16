import { StepCredentials } from '../steps/StepCredentials'
import { StepPrereqs } from '../steps/StepPrereqs'
import { StepVerify } from '../steps/StepVerify'

import type { PlatformDescriptor } from './types'

export const discordDescriptor: PlatformDescriptor = {
  id: 'discord',
  labelKey: 'im.platform.discord.label',
  iconName: 'MessageCircle',
  live: true,
  prereqs: [
    {
      key: 'app',
      labelKey: 'im.wizard.discord.prereq.app',
      helpUrl: () => 'https://discord.com/developers/applications',
    },
    {
      key: 'bot',
      labelKey: 'im.wizard.discord.prereq.bot',
    },
    {
      key: 'intents',
      labelKey: 'im.wizard.discord.prereq.intents',
      items: ['Message Content Intent'],
    },
    {
      key: 'permissions',
      labelKey: 'im.wizard.discord.prereq.permissions',
      items: [
        'View Channels',
        'Send Messages',
        'Send Messages in Threads',
        'Read Message History',
        'Add Reactions',
        'Embed Links',
        'Use Slash Commands',
      ],
    },
    {
      key: 'invite',
      labelKey: 'im.wizard.discord.prereq.invite',
    },
  ],
  credentialFields: [
    {
      key: 'bot_token',
      labelKey: 'im.wizard.discord.field.botToken',
      type: 'password',
      required: true,
    },
    {
      key: 'application_id',
      labelKey: 'im.wizard.discord.field.applicationId',
      type: 'text',
      required: true,
      placeholder: '123456789012345678',
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
      canAdvance: (f) => !!(f.bot_token && f.application_id),
    },
    {
      key: 'verify',
      labelKey: 'im.wizard.step.verify',
      Component: StepVerify,
    },
  ],
  buildPayload: (f) => ({
    platform: 'discord' as const,
    bot_token: f.bot_token || '',
    application_id: f.application_id || '',
    acting_user_id: 'self',
  }),
  scopeConsoleUrl: (appId) => `https://discord.com/developers/applications/${appId}/bot`,
}

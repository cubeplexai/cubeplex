import { StepCredentials } from '../steps/StepCredentials'
import { StepPrereqs } from '../steps/StepPrereqs'
import { StepVerify } from '../steps/StepVerify'
import type { PlatformDescriptor } from './types'

export const wecomDescriptor: PlatformDescriptor = {
  id: 'wecom',
  labelKey: 'im.platform.wecom.label',
  iconName: 'wecom',
  live: true,
  prereqs: [
    {
      key: 'bot',
      labelKey: 'im.wizard.wecom.prereq.bot',
      helpUrl: () => 'https://work.weixin.qq.com/wework_admin/frame#apps',
    },
    {
      key: 'websocket',
      labelKey: 'im.wizard.wecom.prereq.websocket',
    },
    {
      key: 'credentials',
      labelKey: 'im.wizard.wecom.prereq.credentials',
    },
  ],
  credentialFields: [
    {
      key: 'bot_id',
      labelKey: 'im.wizard.wecom.field.botId',
      type: 'text',
      required: true,
    },
    {
      key: 'secret',
      labelKey: 'im.wizard.wecom.field.secret',
      type: 'password',
      required: true,
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
      canAdvance: (form) => !!(form.bot_id && form.secret),
    },
    {
      key: 'verify',
      labelKey: 'im.wizard.step.verify',
      Component: StepVerify,
    },
  ],
  buildPayload: (form) => ({
    platform: 'wecom' as const,
    bot_id: form.bot_id || '',
    secret: form.secret || '',
    acting_user_id: 'self',
  }),
  identityField: 'bot_id',
  scopeConsoleUrl: () => 'https://work.weixin.qq.com/wework_admin/frame#apps',
}

import { StepDingtalkCredentials } from '../steps/StepDingtalkCredentials'
import { StepPrereqs } from '../steps/StepPrereqs'
import { StepVerify } from '../steps/StepVerify'

import type { PlatformDescriptor } from './types'

export const dingtalkDescriptor: PlatformDescriptor = {
  id: 'dingtalk',
  labelKey: 'im.platform.dingtalk.label',
  iconName: 'dingtalk',
  live: true,
  prereqs: [
    {
      key: 'app',
      labelKey: 'im.wizard.dingtalk.prereq.app',
      helpUrl: () => 'https://open-dev.dingtalk.com/fe/app?hash=%23%2Fcorp%2Fapp#/corp/app',
    },
    {
      key: 'stream',
      labelKey: 'im.wizard.dingtalk.prereq.stream',
    },
    {
      key: 'permissions',
      labelKey: 'im.wizard.dingtalk.prereq.permissions',
      items: [
        'qyapi_chat_manage',
        'qyapi_microapp_manage',
        'Card.Streaming.Write',
        'Card.Instance.Write',
        'Contact.User.Read',
      ],
    },
    {
      key: 'credentials',
      labelKey: 'im.wizard.dingtalk.prereq.credentials',
    },
  ],
  credentialFields: [
    {
      key: 'app_key',
      labelKey: 'im.wizard.dingtalk.field.appKey',
      type: 'text',
      required: true,
      placeholder: 'ding...',
    },
    {
      key: 'app_secret',
      labelKey: 'im.wizard.dingtalk.field.appSecret',
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
      Component: StepDingtalkCredentials,
      canAdvance: (f) => !!(f.app_key && f.app_secret),
    },
    {
      key: 'verify',
      labelKey: 'im.wizard.step.verify',
      Component: StepVerify,
    },
  ],
  buildPayload: (f) => ({
    platform: 'dingtalk' as const,
    app_key: f.app_key || '',
    app_secret: f.app_secret || '',
    bot_name: f.bot_name || '',
    bot_avatar_url: f.bot_avatar_url || '',
    acting_user_id: 'self',
  }),
  scopeConsoleUrl: () => 'https://open-dev.dingtalk.com/fe/app?hash=%23%2Fcorp%2Fapp#/corp/app',
}

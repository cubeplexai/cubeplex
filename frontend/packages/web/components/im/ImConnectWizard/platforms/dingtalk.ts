import { StepCredentials } from '../steps/StepCredentials'
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
      helpUrl: () => 'https://open.dingtalk.com/document/orgapp/create-orgapp',
    },
    {
      key: 'stream',
      labelKey: 'im.wizard.dingtalk.prereq.stream',
    },
    {
      key: 'permissions',
      labelKey: 'im.wizard.dingtalk.prereq.permissions',
      items: ['qyapi_chat_manage', 'qyapi_robot_sendmsg', 'Contact.User.Read'],
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
      Component: StepCredentials,
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
    acting_user_id: 'self',
  }),
  scopeConsoleUrl: () => 'https://open.dingtalk.com',
}

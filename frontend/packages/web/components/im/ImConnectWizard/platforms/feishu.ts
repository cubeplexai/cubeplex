import { StepCredentials } from '../steps/StepCredentials'
import { StepPrereqs } from '../steps/StepPrereqs'
import { StepVerify } from '../steps/StepVerify'

import type { PlatformDescriptor } from './types'

export const feishuDescriptor: PlatformDescriptor = {
  id: 'feishu',
  labelKey: 'im.platform.feishu.label',
  iconName: 'MessageSquare',
  live: true,
  prereqs: [
    {
      key: 'app',
      labelKey: 'im.wizard.feishu.prereq.app',
      helpUrl: () => 'https://open.feishu.cn/',
    },
    { key: 'bot', labelKey: 'im.wizard.feishu.prereq.bot' },
    {
      key: 'scopes',
      labelKey: 'im.wizard.feishu.prereq.scopes',
      helpUrl: (f) =>
        `https://open.feishu.cn/app/${
          f.app_id || ''
        }/auth?q=contact:user.email:readonly,contact:user.id:readonly,im:message`,
    },
    { key: 'published', labelKey: 'im.wizard.feishu.prereq.published' },
  ],
  credentialFields: [
    {
      key: 'app_id',
      labelKey: 'im.wizard.feishu.field.appId',
      type: 'text',
      required: true,
      placeholder: 'cli_xxx',
    },
    {
      key: 'app_secret',
      labelKey: 'im.wizard.feishu.field.appSecret',
      type: 'password',
      required: true,
    },
    {
      key: 'delivery_mode',
      labelKey: 'im.wizard.feishu.field.deliveryMode',
      type: 'select',
      required: true,
      options: [
        {
          value: 'long_connection',
          labelKey: 'im.wizard.feishu.deliveryMode.long_connection',
        },
        { value: 'webhook', labelKey: 'im.wizard.feishu.deliveryMode.webhook' },
      ],
    },
    {
      key: 'domain',
      labelKey: 'im.wizard.feishu.field.domain',
      type: 'select',
      required: true,
      options: [
        { value: 'feishu', labelKey: 'im.wizard.feishu.domain.feishu' },
        { value: 'lark', labelKey: 'im.wizard.feishu.domain.lark' },
      ],
    },
    {
      key: 'encrypt_key',
      labelKey: 'im.wizard.feishu.field.encryptKey',
      type: 'password',
      required: false,
      showIf: (f) => f.delivery_mode === 'webhook',
    },
    {
      key: 'verification_token',
      labelKey: 'im.wizard.feishu.field.verificationToken',
      type: 'password',
      required: false,
      showIf: (f) => f.delivery_mode === 'webhook',
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
      canAdvance: (f) =>
        !!(f.app_id && f.app_secret && f.delivery_mode && f.domain) && f.app_id.startsWith('cli_'),
    },
    {
      key: 'verify',
      labelKey: 'im.wizard.step.verify',
      Component: StepVerify,
    },
  ],
  buildPayload: (f) => ({
    platform: 'feishu',
    app_id: f.app_id || '',
    app_secret: f.app_secret || '',
    delivery_mode: (f.delivery_mode as 'long_connection' | 'webhook') || 'long_connection',
    domain: (f.domain as 'feishu' | 'lark') || 'feishu',
    encrypt_key: f.encrypt_key || '',
    verification_token: f.verification_token || '',
    acting_user_id: 'self',
  }),
  scopeConsoleUrl: (appId) =>
    `https://open.feishu.cn/app/${appId}/auth?q=contact:user.email:readonly,contact:user.id:readonly,im:message`,
}

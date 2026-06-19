import { StepCredentials } from '../steps/StepCredentials'
import { StepPrereqs } from '../steps/StepPrereqs'
import { StepVerify } from '../steps/StepVerify'
import type { PlatformDescriptor } from './types'

export const teamsDescriptor: PlatformDescriptor = {
  id: 'teams',
  labelKey: 'im.platform.teams.label',
  iconName: 'MessageSquare',
  live: true,
  prereqs: [
    {
      key: 'app',
      labelKey: 'im.wizard.teams.prereq.app',
      helpUrl: () => 'https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps',
    },
    {
      key: 'graphPermission',
      labelKey: 'im.wizard.teams.prereq.graphPermission',
    },
    {
      key: 'clientSecret',
      labelKey: 'im.wizard.teams.prereq.clientSecret',
    },
    {
      key: 'endpoint',
      labelKey: 'im.wizard.teams.prereq.endpoint',
    },
  ],
  credentialFields: [
    {
      key: 'app_id',
      labelKey: 'im.wizard.teams.field.appId',
      type: 'text',
      required: true,
      placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
    },
    {
      key: 'app_secret',
      labelKey: 'im.wizard.teams.field.appSecret',
      type: 'password',
      required: true,
      placeholder: '',
    },
    {
      key: 'tenant_id',
      labelKey: 'im.wizard.teams.field.tenantId',
      type: 'text',
      required: true,
      placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
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
      canAdvance: (f) => !!(f.app_id && f.app_secret && f.tenant_id),
    },
    {
      key: 'verify',
      labelKey: 'im.wizard.step.verify',
      Component: StepVerify,
    },
  ],
  buildPayload: (f) => ({
    platform: 'teams' as const,
    app_id: f.app_id || '',
    app_secret: f.app_secret || '',
    tenant_id: f.tenant_id || '',
    acting_user_id: 'self',
  }),
  scopeConsoleUrl: () => 'https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps',
}

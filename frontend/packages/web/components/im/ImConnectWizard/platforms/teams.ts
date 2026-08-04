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
      // Create Azure Bot (not bare App registrations). Incomplete
      // #view/Microsoft_AAD_RegisteredApps dumps users on the portal home.
      helpUrl: () => 'https://portal.azure.com/#create/Microsoft.AzureBot',
    },
    {
      key: 'clientSecret',
      labelKey: 'im.wizard.teams.prereq.clientSecret',
      // Secrets live on the Entra app behind the bot.
      helpUrl: () =>
        'https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade',
    },
    {
      key: 'graphPermission',
      labelKey: 'im.wizard.teams.prereq.graphPermission',
      items: ['User.Read.All'],
      helpUrl: () =>
        'https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade',
    },
    {
      key: 'endpoint',
      labelKey: 'im.wizard.teams.prereq.endpoint',
    },
    {
      key: 'teamsChannel',
      labelKey: 'im.wizard.teams.prereq.teamsChannel',
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
  // Secrets + Graph permissions live on the Entra app registration list.
  scopeConsoleUrl: () =>
    'https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade',
}

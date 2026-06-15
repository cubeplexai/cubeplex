export { feishuDescriptor } from './feishu'
export { discordDescriptor } from './discord'
export { slackDescriptor } from './slack.stub'
export { teamsDescriptor } from './teams.stub'
export type {
  PlatformDescriptor,
  WizardStepDef,
  WizardStepProps,
  FieldDef,
  FormState,
} from './types'

import { discordDescriptor } from './discord'
import { feishuDescriptor } from './feishu'
import { slackDescriptor } from './slack.stub'
import { teamsDescriptor } from './teams.stub'
import type { PlatformDescriptor } from './types'

export const ALL_PLATFORMS: PlatformDescriptor[] = [
  feishuDescriptor,
  discordDescriptor,
  slackDescriptor,
  teamsDescriptor,
]

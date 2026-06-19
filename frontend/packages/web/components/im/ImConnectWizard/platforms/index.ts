export { feishuDescriptor } from './feishu'
export { discordDescriptor } from './discord'
export { slackDescriptor } from './slack'
export { dingtalkDescriptor } from './dingtalk'
export { teamsDescriptor } from './teams'
export type {
  PlatformDescriptor,
  WizardStepDef,
  WizardStepProps,
  FieldDef,
  FormState,
} from './types'

import { dingtalkDescriptor } from './dingtalk'
import { discordDescriptor } from './discord'
import { feishuDescriptor } from './feishu'
import { slackDescriptor } from './slack'
import { teamsDescriptor } from './teams'
import type { PlatformDescriptor } from './types'

export const ALL_PLATFORMS: PlatformDescriptor[] = [
  feishuDescriptor,
  discordDescriptor,
  slackDescriptor,
  dingtalkDescriptor,
  teamsDescriptor,
]

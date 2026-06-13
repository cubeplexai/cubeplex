export { feishuDescriptor } from './feishu'
export { slackDescriptor } from './slack.stub'
export type {
  PlatformDescriptor,
  WizardStepDef,
  WizardStepProps,
  FieldDef,
  FormState,
} from './types'

import { feishuDescriptor } from './feishu'
import { slackDescriptor } from './slack.stub'
import type { PlatformDescriptor } from './types'

export const ALL_PLATFORMS: PlatformDescriptor[] = [feishuDescriptor, slackDescriptor]

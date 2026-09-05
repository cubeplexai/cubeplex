import { describe, expect, it } from 'vitest'

import { classifyConnectError } from './useConnectMutation'

describe('classifyConnectError', () => {
  it('maps WeCom authentication errors to its Secret field', () => {
    expect(classifyConnectError(400, null, 'wecom')).toMatchObject({
      shape: 'field',
      field: 'secret',
    })
  })

  it('keeps retryable and shared failures out of credential fields', () => {
    expect(classifyConnectError(503, null, 'wecom').shape).toBe('banner')
    expect(classifyConnectError(409, null, 'wecom').shape).toBe('banner')
    expect(classifyConnectError(0, null, 'wecom').shape).toBe('toast')
  })
})

import { describe, expect, it } from 'vitest'
import { ATTACHMENT_PROXY_BODY_LIMIT, nextConfig } from '../../next.config'

describe('next config', () => {
  it('allows attachment upload requests above the default 10MB proxy limit', () => {
    expect(ATTACHMENT_PROXY_BODY_LIMIT).toBe('60mb')
    expect(nextConfig.experimental?.proxyClientMaxBodySize).toBe(ATTACHMENT_PROXY_BODY_LIMIT)
  })
})

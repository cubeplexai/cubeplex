import { describe, expect, it } from 'vitest'
import { formatProbeDetail } from './probeDetail'

describe('formatProbeDetail', () => {
  it('collapses a 402 blob to status + upstream message', () => {
    const raw =
      '[probe/deepseek/deepseek-v4-flash:free @ https://openrouter.ai/api/v1/] ' +
      "APIStatusError: Error code: 402 - {'error': {'message': 'Provider returned error', " +
      "'code': 402, 'metadata': {'raw': '{\"error\":{\""
    expect(formatProbeDetail(raw)).toBe('HTTP 402 · Provider returned error')
  })

  it('handles a 404 model-removed message', () => {
    const raw =
      '[probe/stepfun:free @ .../] NotFoundError: Error code: 404 - ' +
      "{'error': {'message': 'No endpoints found for stepfun:free.', 'code': 404}}"
    expect(formatProbeDetail(raw)).toBe('HTTP 404 · No endpoints found for stepfun:free.')
  })

  it('handles a double-quoted JSON message', () => {
    const raw = 'Error code: 401 - {"error": {"message": "Missing Authentication header"}}'
    expect(formatProbeDetail(raw)).toBe('HTTP 401 · Missing Authentication header')
  })

  it('extracts a 401 auth message that contains a URL', () => {
    const raw =
      '[probe/qwen3-max @ https://dashscope-intl.aliyuncs.com/compatible-mode/v1/] ' +
      "AuthenticationError: Error code: 401 - {'error': {'message': 'Incorrect API key provided. " +
      "For details, see: https://www.alibabacloud.com/help/x', 'type': 'invalid_request_error'}}"
    expect(formatProbeDetail(raw)).toBe(
      'HTTP 401 · Incorrect API key provided. For details, see: https://www.alibabacloud.com/help/x',
    )
  })

  it('falls back to HTTP status when no message is extractable', () => {
    expect(formatProbeDetail('APIStatusError: Error code: 503 - {garbage')).toBe(
      'APIStatusError: Error code: 503',
    )
  })

  it('leaves an already-clean advisory detail untouched', () => {
    expect(formatProbeDetail('no usage block → cost recorded as zero')).toBe(
      'no usage block → cost recorded as zero',
    )
  })

  it('returns empty string for nullish input', () => {
    expect(formatProbeDetail(null)).toBe('')
    expect(formatProbeDetail(undefined)).toBe('')
  })
})

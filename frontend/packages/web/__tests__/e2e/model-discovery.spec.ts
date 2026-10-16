import { createServer } from 'node:http'
import { test, expect } from '@playwright/test'
import { registerAndLand } from './_helpers/auth'

for (const theme of ['light', 'dark'] as const) {
  test(`CLIProxyAPI discovery imports, verifies, and enables models with their saved limits (${theme})`, async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme: theme })
    const requests: Record<string, unknown>[] = []
    const upstream = createServer((request, response) => {
      if (request.method === 'GET' && request.url?.startsWith('/v1/models')) {
        response.setHeader('Content-Type', 'application/json')
        response.end(
          JSON.stringify({
            models: [],
            data: ['alias-chat-a', 'alias-chat-b'].map((id) => ({
              id,
              purpose: 'chat',
              context_length: 8192,
              max_tokens: 128,
              input_modalities: ['text'],
              reasoning: false,
            })),
          }),
        )
        return
      }
      let raw = ''
      request.on('data', (chunk) => {
        raw += chunk
      })
      request.on('end', () => {
        const body = JSON.parse(raw)
        requests.push(body)
        const delta = body.tools
          ? {
              tool_calls: [
                {
                  index: 0,
                  id: 'echo',
                  type: 'function',
                  function: { name: 'echo', arguments: '{"text":"ping"}' },
                },
              ],
            }
          : { content: 'OK' }
        const chunk = {
          id: 'completion',
          object: 'chat.completion.chunk',
          created: 1,
          model: body.model,
          choices: [{ index: 0, delta, finish_reason: null }],
        }
        response.writeHead(200, { 'Content-Type': 'text/event-stream' })
        response.end(
          `data: ${JSON.stringify(chunk)}\n\ndata: ${JSON.stringify({
            ...chunk,
            choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
            usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
          })}\n\ndata: [DONE]\n\n`,
        )
      })
    })
    await new Promise<void>((resolve) => upstream.listen(0, '127.0.0.1', resolve))
    const address = upstream.address()
    if (!address || typeof address === 'string') throw new Error('Missing upstream port')
    const name = `discovery-browser-${Date.now()}`
    try {
      await registerAndLand(page)
      await page.goto('/admin/models/new')
      await page.getByRole('button', { name: 'CLIProxyAPI', exact: true }).click()
      await page.getByRole('button', { name: 'Next', exact: true }).click()
      await page.getByLabel('Name', { exact: true }).fill(name)
      await page.getByLabel('Base URL', { exact: true }).fill(`http://127.0.0.1:${address.port}/v1`)
      await page.getByText('None', { exact: true }).click()
      await page.getByTestId('provider-config-submit').click()
      await page.getByRole('button', { name: 'List available models' }).click()
      await expect(page.getByText('2 models returned')).toBeVisible()
      await page.screenshot({ path: `test-results/discovery-preview-${theme}.png`, fullPage: true })
      await page.getByRole('button', { name: 'Import 2 selected models' }).click()
      await expect(page.getByRole('button', { name: 'Finish', exact: true })).toBeEnabled({
        timeout: 60_000,
      })
      await page.getByRole('button', { name: 'Finish', exact: true }).click()
      await expect(page).toHaveURL(/\/admin\/models$/)
      const card = page.getByTestId(`provider-card-${name}`)
      await card.click()
      await expect(page.getByTestId('model-row-alias-chat-a')).toBeVisible()
      await expect(page.getByTestId('model-row-alias-chat-b')).toBeVisible()
      const saved = await page.request.get('/api/v1/admin/providers')
      const providers = await saved.json()
      const provider = providers.find((p: { name: string }) => p.name === name)
      const detail = await (await page.request.get(`/api/v1/admin/providers/${provider.id}`)).json()
      expect(detail.models.every((m: { enabled: boolean }) => m.enabled)).toBe(true)
      expect(requests.length).toBeGreaterThan(0)
      expect(requests.every((body) => Number(body.max_tokens) <= 128)).toBe(true)
      expect(
        requests.every((body) => ['alias-chat-a', 'alias-chat-b'].includes(String(body.model))),
      ).toBe(true)
      await page.getByTestId('provider-delete-button').click()
      await page.getByTestId('provider-delete-confirm').click()
      await expect(card).toBeHidden()
    } finally {
      await new Promise<void>((resolve, reject) =>
        upstream.close((error) => (error ? reject(error) : resolve())),
      )
    }
  })
}

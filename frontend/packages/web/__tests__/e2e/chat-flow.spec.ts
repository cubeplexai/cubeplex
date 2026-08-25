import { test, expect, type Page } from '@playwright/test'
import { REAL_LLM_TAG, registerAndLand, skipWithoutRealLlm } from './_helpers/auth'

async function csrfToken(page: Page): Promise<string> {
  const cookies = await page.context().cookies()
  return cookies.find((cookie) => cookie.name.startsWith('cubeplex_csrf'))?.value ?? ''
}

test('shows progress while opening conversation history', async ({ page }) => {
  const { wsId } = await registerAndLand(page)
  const createResponse = await page.request.post(
    `/api/v1/ws/${wsId}/conversations?title=Loading%20history`,
    { headers: { 'X-CSRF-Token': await csrfToken(page) } },
  )
  expect(createResponse.status()).toBe(201)
  const conversationId = ((await createResponse.json()) as { id: string }).id

  let historyResponseReady!: () => void
  let releaseHistoryResponse!: () => void
  const responseReady = new Promise<void>((resolve) => {
    historyResponseReady = resolve
  })
  const releaseResponse = new Promise<void>((resolve) => {
    releaseHistoryResponse = resolve
  })
  await page.route(
    `**/api/v1/ws/${wsId}/conversations/${conversationId}/bootstrap`,
    async (route) => {
      const response = await route.fetch()
      expect(response.ok()).toBe(true)
      historyResponseReady()
      await releaseResponse
      await route.fulfill({ response })
    },
  )

  await page.goto(`/w/${wsId}/conversations/${conversationId}`)
  await responseReady

  const loadingStatus = page.getByRole('status', { name: 'Loading conversation…' })
  await expect(loadingStatus).toBeVisible()

  releaseHistoryResponse()
  await expect(loadingStatus).toBeHidden()
  await expect(page.getByTestId('chat-input')).toBeVisible()
})

test('can send a message and see a response', { tag: REAL_LLM_TAG }, async ({ page }) => {
  skipWithoutRealLlm()
  await registerAndLand(page)

  const input = page.getByPlaceholder('Tell CubePlex what you want to get done…')
  await input.fill('Say the word "hello" and nothing else.')
  await input.press('Enter')

  await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//, { timeout: 10_000 })

  const main = page.getByRole('main')
  await expect(main.getByText('Say the word "hello" and nothing else.')).toBeVisible({
    timeout: 10_000,
  })

  await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 50_000 })

  const assistantMsg = main.locator('[data-role="assistant"]')
  await expect(assistantMsg).toBeVisible()
  const text = await assistantMsg.textContent()
  expect(text!.trim().length).toBeGreaterThan(0)
})

test('conversation history persists after page reload', { tag: REAL_LLM_TAG }, async ({ page }) => {
  skipWithoutRealLlm()
  await registerAndLand(page)

  const input = page.getByPlaceholder('Tell CubePlex what you want to get done…')
  await input.fill('My favorite color is blue.')
  await input.press('Enter')

  await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//)
  await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 50_000 })

  await page.reload()

  const main = page.getByRole('main')
  await expect(main.getByText('My favorite color is blue.')).toBeVisible({ timeout: 10_000 })
  // The agent may render multiple [data-role="assistant"] nodes per
  // run (thinking / tool calls + final response). The history-reload
  // assertion only cares that AT LEAST one survives the reload; use
  // .first() to keep the assertion robust against multi-step replies.
  await expect(main.locator('[data-role="assistant"]').first()).toBeVisible()
})

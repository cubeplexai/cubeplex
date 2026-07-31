import { test, expect } from '@playwright/test'
import { registerAndLand } from './_helpers/auth'

test('workspace switching isolates conversation lists', async ({ page }) => {
  await registerAndLand(page)
  const firstWsUrl = page.url()
  const firstWsId = firstWsUrl.split('/w/')[1]

  const input = page.getByPlaceholder('Tell CubePlex what you want to get done…')
  const sendButton = page.getByTestId('send-button')
  // The URL changes before the client shell finishes hydrating, and the first
  // dev-server render can remount the composer. Refill until React has accepted
  // the draft instead of sending Enter to a server-rendered textarea.
  await expect(async () => {
    await input.fill('Hello in workspace 1')
    await expect(sendButton).toBeEnabled({ timeout: 500 })
  }).toPass({ timeout: 10_000 })

  const conversationCreated = page.waitForResponse((response) => {
    const path = new URL(response.url()).pathname
    return (
      response.request().method() === 'POST' &&
      path === `/api/v1/ws/${firstWsId}/conversations` &&
      response.status() === 201
    )
  })
  await input.press('Enter')
  const createResponse = await conversationCreated
  const conversation = (await createResponse.json()) as { id: string }
  const convInWs1Url = `/w/${firstWsId}/conversations/${conversation.id}`
  await expect(page).toHaveURL(convInWs1Url)

  await page.goto('/workspaces')
  await expect(page.getByRole('link', { name: 'Open' })).toBeVisible({ timeout: 10_000 })
  await page.getByPlaceholder('e.g. Side project').fill('Side')
  await page.getByRole('button', { name: /create workspace/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/)
  const secondWsUrl = page.url()
  const secondWsId = secondWsUrl.split('/w/')[1]
  expect(secondWsId).not.toBe(firstWsId)

  const wrongUrl = convInWs1Url.replace(`/w/${firstWsId}/`, `/w/${secondWsId}/`)
  await page.goto(wrongUrl)
  await expect(page.getByText(/conversation not found/i)).toBeVisible({ timeout: 10_000 })
})

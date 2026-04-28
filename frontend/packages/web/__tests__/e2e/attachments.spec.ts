import { test, expect, type Page } from '@playwright/test'
import path from 'node:path'
import fs from 'node:fs'

const PASSWORD = 'correcthorsebatterystaple'

async function registerAndLand(page: Page): Promise<void> {
  const email = `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
}

test.describe('M7 attachments happy path', () => {
  // This test exercises the full attachment upload + send cycle including LLM response.
  // Allow up to 3 minutes to accommodate slow LLM endpoints.
  test.setTimeout(180_000)

  test('upload image, send, see attachment in history', async ({ page }) => {
    await registerAndLand(page)

    // The workspace home InputBar has no conversationId — the attach button is disabled.
    // First create a conversation by sending a plain message, then navigate to the
    // conversation page where InputBar receives a conversationId and the attach button
    // becomes enabled.
    const input = page.getByPlaceholder('有什么可以帮你的？')
    await input.fill('hello')
    await input.press('Enter')

    // Wait for navigation to the conversation page
    await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//, { timeout: 10_000 })

    // Wait for loading indicator to disappear (first message stream complete)
    await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 90_000 })

    // Prepare a tiny valid PNG on disk
    const tmp = path.join(__dirname, '__tmp_atta.png')
    fs.writeFileSync(
      tmp,
      Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
        'base64',
      ),
    )

    try {
      // Click the paperclip and pick the file
      const fileChooserPromise = page.waitForEvent('filechooser')
      await page.getByRole('button', { name: 'Attach files' }).click()
      const fc = await fileChooserPromise
      await fc.setFiles(tmp)

      // Chip appears (the remove button is the chip's tell)
      await expect(page.locator('[aria-label^="Remove"]').first()).toBeVisible()

      // Wait for upload to complete: the Loader2 spinner (.animate-spin) inside the chip
      // disappears once the upload finishes and the server file ID is stored in state.
      // Without this wait, attachedIds() may still be empty when send is clicked.
      await expect(page.locator('.animate-spin').first()).toBeHidden({ timeout: 10_000 })

      // Type and send. Use a prompt that the LLM will answer quickly without
      // triggering sandbox tool calls (avoiding the 1-2 minute sandbox warm-up).
      await page.locator('textarea').fill('Reply with the single word OK')
      await page.getByTestId('send-button').click()

      // Wait for the LLM run to finish. After the stream ends, the user message
      // (with attachment metadata) is persisted in the LangGraph checkpoint.
      await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 60_000 })

      // Reload to fetch fresh history from the bootstrap endpoint. Without an
      // active run, loadMessages returns the full checkpoint including the user
      // message with its attachment metadata.
      await page.reload()

      // History shows attachment
      await expect(page.getByTestId('message-attachments').first()).toBeVisible({
        timeout: 15_000,
      })
    } finally {
      try {
        fs.unlinkSync(tmp)
      } catch {
        // ignore
      }
    }
  })
})

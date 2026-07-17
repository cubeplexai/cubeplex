import { test, expect } from '@playwright/test'
import path from 'node:path'
import fs from 'node:fs'
import { registerAndLand, skipWithoutRealLlm } from './_helpers/auth'

test.describe('M7 attachments happy path', () => {
  // This test exercises the full attachment upload + send cycle including LLM response.
  // Allow up to 3 minutes to accommodate slow LLM endpoints.
  test.setTimeout(180_000)

  test('upload image, send, see attachment in history', async ({ page }) => {
    skipWithoutRealLlm()
    await registerAndLand(page)

    // The workspace home InputBar has no conversationId — the attach button is disabled.
    // First create a conversation by sending a plain message, then navigate to the
    // conversation page where InputBar receives a conversationId and the attach button
    // becomes enabled.
    const input = page.getByTestId('chat-input')
    await input.fill('hello')
    await input.press('Enter')

    // Wait for navigation to the conversation page. 10s has been observed to
    // be too tight on slow CI runners — bumped to keep this from flaking.
    await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//, { timeout: 30_000 })

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
      await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 90_000 })

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

test.describe('M7 attachments — home page eager-create flow', () => {
  test.setTimeout(180_000)

  test('cancels an in-flight upload from the home page', async ({ page }) => {
    await registerAndLand(page)

    // Write a 1MB temp text file (large enough that the upload is cancellable).
    const tmp = path.join(__dirname, '__tmp_cancel.bin')
    fs.writeFileSync(tmp, Buffer.alloc(1024 * 1024, 0))

    try {
      // Pick a file via the file chooser. The home-page InputBar now has
      // onCreateConversation, so this triggers eager-create + upload.
      const fileChooserPromise = page.waitForEvent('filechooser')
      await page.getByRole('button', { name: 'Attach files' }).click()
      const fc = await fileChooserPromise
      await fc.setFiles(tmp)

      // The cancel/remove button on the chip becomes visible immediately.
      const removeBtn = page.locator('[aria-label^="Remove"]').first()
      await expect(removeBtn).toBeVisible({ timeout: 5_000 })

      // Click cancel BEFORE the upload completes. With a 1 MB file this is
      // typically a few hundred ms of network time — plenty for Playwright.
      await removeBtn.click()

      // Chip is gone (no chips remain in the staging area).
      await expect(page.locator('[aria-label^="Remove"]')).toHaveCount(0)
    } finally {
      try {
        fs.unlinkSync(tmp)
      } catch {
        // ignore
      }
    }
  })

  test('uploads on the home page and sends with attachment above the bubble', async ({ page }) => {
    skipWithoutRealLlm()
    await registerAndLand(page)

    // Write a tiny valid PNG inline (re-use the same trick as the existing test).
    const tmp = path.join(__dirname, '__tmp_homeflow.png')
    fs.writeFileSync(
      tmp,
      Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
        'base64',
      ),
    )

    try {
      const fileChooserPromise = page.waitForEvent('filechooser')
      await page.getByRole('button', { name: 'Attach files' }).click()
      const fc = await fileChooserPromise
      await fc.setFiles(tmp)

      // Wait for upload completion: the upload-progress spinner inside the chip
      // disappears when the upload resolves.
      await expect(page.locator('.animate-spin').first()).toBeHidden({ timeout: 15_000 })

      // Send a short prompt.
      await page.locator('textarea').fill('Reply with the single word OK')
      await page.getByTestId('send-button').click()

      // Navigation should land on the conversation page.
      await expect(page).toHaveURL(/\/w\/[^/]+\/conversations\//, { timeout: 10_000 })

      // Wait for the LLM round to finish.
      await expect(page.getByTestId('loading-indicator')).toBeHidden({ timeout: 90_000 })
      await page.reload()

      // After reload, the user message + attachments are in history. Wait
      // for the message text first — that's the most reliable signal that
      // the conversation history has loaded — then check attachments. Doing
      // it the other way around races the history fetch on a cold backend.
      const userMsg = page.getByText('Reply with the single word OK').first()
      await expect(userMsg).toBeVisible({ timeout: 15_000 })

      const attach = page.getByTestId('message-attachments').first()
      await expect(attach).toBeVisible({ timeout: 5_000 })

      // Attachments block is positioned ABOVE the user message in DOM/visual order.
      const userBox = await userMsg.boundingBox()
      const attachBox = await attach.boundingBox()
      expect(userBox).not.toBeNull()
      expect(attachBox).not.toBeNull()
      expect(userBox!.y).toBeGreaterThan(attachBox!.y)
    } finally {
      try {
        fs.unlinkSync(tmp)
      } catch {
        // ignore
      }
    }
  })
})

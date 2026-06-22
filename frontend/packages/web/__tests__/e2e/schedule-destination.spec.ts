import { test, expect, type Page } from '@playwright/test'

const PASSWORD = 'correcthorsebatterystaple'

async function registerAndLand(page: Page): Promise<string> {
  const email = `u-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
  await page.goto('/register')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: /create account/i }).click()
  await expect(page).toHaveURL(/\/w\/[^/]+$/, { timeout: 10_000 })
  const url = new URL(page.url())
  const wsId = url.pathname.split('/')[2]
  return wsId
}

async function getCsrf(page: Page): Promise<string> {
  // Mutating endpoints require the CSRF header read from the cookie. The page
  // request context inherits the cookie jar but does not auto-inject the
  // double-submit header — we mirror what the in-browser ApiClient does.
  const cookies = await page.context().cookies()
  const csrf = cookies.find((c) => c.name.startsWith('cubebox_csrf'))?.value ?? ''
  return csrf
}

async function seedTopic(page: Page, wsId: string, title: string): Promise<string> {
  const csrf = await getCsrf(page)
  // The workspace-scoped endpoint is /api/v1/ws/{wsId}/topics. The frontend
  // ApiClient injects the {wsId} segment automatically; from Playwright we
  // hit the resolved path directly.
  const res = await page.request.post(`/api/v1/ws/${wsId}/topics`, {
    headers: { 'X-CSRF-Token': csrf, 'Content-Type': 'application/json' },
    data: { title },
  })
  expect(res.status()).toBe(201)
  const body = (await res.json()) as { topic: { id: string; title: string } }
  return body.topic.id
}

test('creates schedule pinned to topic via new_each_run', async ({ page }) => {
  const wsId = await registerAndLand(page)
  // Seed a topic *before* opening the dialog so the picker lists it. The
  // picker fetches /api/v1/topics on mount; no topic seed → empty list.
  const topicTitle = `Daily news ${Date.now().toString(36)}`
  const topicId = await seedTopic(page, wsId, topicTitle)

  await page.goto(`/w/${wsId}/scheduled-tasks`)
  await page.getByTestId('new-task-button').click()
  await expect(page.getByTestId('task-form-dialog')).toBeVisible()

  await page.getByLabel('Name').fill('Topic-pinned daily')
  await page.getByLabel('Prompt').fill('Summarize today and post into the topic')

  // The dialog defaults to schedule_kind=cron Daily 09:00 and target_mode=new_each_run.
  // Confirm the radio is selected and pick the topic.
  const topicOption = page.getByTestId('destination-option-new_each_run')
  await expect(topicOption.locator('input[type="radio"]')).toBeChecked()

  await page.getByTestId('topic-picker-trigger').click()
  await page.getByTestId(`topic-option-${topicId}`).click()

  // Capture the create POST so we can assert the destination shape regardless
  // of whatever the UI happens to render afterwards. waitForRequest must be
  // armed *before* the click that fires it.
  const postPromise = page.waitForRequest(
    (req) => req.method() === 'POST' && req.url().endsWith(`/api/v1/ws/${wsId}/scheduled-tasks`),
  )
  await page.getByRole('button', { name: /create task/i }).click()
  const postReq = await postPromise

  const postBody = postReq.postDataJSON() as Record<string, unknown>
  expect(postBody.target_mode).toBe('new_each_run')
  expect(postBody.topic_id).toBe(topicId)
  // Negative checks: a new_each_run create must not smuggle in im_* fields.
  expect(postBody.im_account_id).toBeUndefined()
  expect(postBody.im_channel_id).toBeUndefined()
  expect(postBody.target_conversation_id).toBeUndefined()

  // The list row's destination chip surfaces the topic title — the *user-visible
  // contract* that breaks if the DestinationCell stops calling getTopic or the
  // backend drops topic_id on the response. Scoped to the freshly-created card.
  await expect(page.getByTestId('task-form-dialog')).not.toBeVisible({ timeout: 5_000 })
  await expect(page.getByText('Topic-pinned daily')).toBeVisible({ timeout: 8_000 })
  const topicChip = page.getByTestId('destination-topic').first()
  await expect(topicChip).toBeVisible({ timeout: 8_000 })
  await expect(topicChip).toContainText(topicTitle)
})

// Flow 2 — IM-channel schedule is read-only on edit.
//
// Skipped: an im_channel schedule requires both an IMConnectorAccount row and
// the channel-binding rows it points at. The only path to create an IM account
// is `POST /api/v1/ws/{wsId}/im/accounts`, which runs a platform-specific
// OAuth/credentials handshake (Feishu/Slack/Discord/etc.) — there is no
// "fake account" seed in the public API and Playwright can't reach the DB.
//
// The invariants this flow would protect are already covered by:
//   * backend e2e — backend/tests/e2e/test_scheduled_task_destinations.py
//     (im_channel target_mode validation, PATCH rejection of im_* mutations,
//     dispatch branches);
//   * unit/integration — components are wired such that
//     ScheduledTaskFormDialog.tsx flips `isReadOnlyDestination` purely from
//     `task.target_mode === 'im_channel'` and the PATCH payload only sends
//     prompt/name/schedule fields (see lines 109-121 of the file).
//
// Re-enabling this needs either: (a) a test-only IM-account seed endpoint,
// or (b) Playwright running inside the same Python process so it can reach
// `_seed_im_account()` from the backend test helpers.
test.skip('im_channel schedule destination is read-only on edit', () => {
  // Body intentionally empty — see comment block above for the cover story
  // and the unblock paths.
})

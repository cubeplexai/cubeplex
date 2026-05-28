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

async function getApiBase(page: Page): Promise<string> {
  return process.env.PLAYWRIGHT_API_BASE ?? page.url().split('/w/')[0]
}

async function getCookies(page: Page): Promise<{ cookieHeader: string; csrf: string }> {
  const cookies = await page.context().cookies()
  const cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join('; ')
  const csrf = cookies.find((c) => c.name.startsWith('cubebox_csrf'))?.value ?? ''
  return { cookieHeader, csrf }
}

test('Scheduled Tasks: shows page title and empty state', async ({ page }) => {
  const wsId = await registerAndLand(page)
  await page.goto(`/w/${wsId}/scheduled-tasks`)

  await expect(page.getByRole('heading', { name: 'Scheduled Tasks', exact: true })).toBeVisible()
  await expect(page.getByTestId('empty-state')).toBeVisible()
})

test('Scheduled Tasks: create → appears in list → pause → resume → delete', async ({
  page,
  request,
}) => {
  const wsId = await registerAndLand(page)

  // Seed task via API so we don't depend on form UI for most assertions
  const apiBase = await getApiBase(page)
  const { cookieHeader, csrf } = await getCookies(page)

  const createRes = await request.post(`${apiBase}/api/v1/ws/${wsId}/scheduled-tasks`, {
    headers: {
      'X-CSRF-Token': csrf,
      Cookie: cookieHeader,
      'Content-Type': 'application/json',
    },
    data: {
      name: 'E2E test task',
      prompt: 'Say hello',
      schedule_kind: 'interval',
      interval_seconds: 3600,
      target_mode: 'new_each_run',
    },
  })
  expect(createRes.status()).toBe(201)
  const created = (await createRes.json()) as { id: string }

  await page.goto(`/w/${wsId}/scheduled-tasks`)

  // Task card appears
  await expect(page.getByTestId(`task-card-${created.id}`)).toBeVisible({ timeout: 8_000 })
  await expect(page.getByTestId(`status-badge-${created.id}`)).toHaveText('Active')

  // Pause via API, reload, verify badge
  const pauseRes = await request.post(
    `${apiBase}/api/v1/ws/${wsId}/scheduled-tasks/${created.id}/pause`,
    {
      headers: { 'X-CSRF-Token': csrf, Cookie: cookieHeader, 'Content-Type': 'application/json' },
      data: {},
    },
  )
  expect(pauseRes.status()).toBe(200)

  await page.reload()
  await expect(page.getByTestId(`status-badge-${created.id}`)).toHaveText('Paused', {
    timeout: 8_000,
  })

  // Resume via API, reload, verify badge
  const resumeRes = await request.post(
    `${apiBase}/api/v1/ws/${wsId}/scheduled-tasks/${created.id}/resume`,
    {
      headers: { 'X-CSRF-Token': csrf, Cookie: cookieHeader, 'Content-Type': 'application/json' },
      data: {},
    },
  )
  expect(resumeRes.status()).toBe(200)

  await page.reload()
  await expect(page.getByTestId(`status-badge-${created.id}`)).toHaveText('Active', {
    timeout: 8_000,
  })

  // Delete via API, reload, verify gone
  const deleteRes = await request.delete(
    `${apiBase}/api/v1/ws/${wsId}/scheduled-tasks/${created.id}`,
    {
      headers: { 'X-CSRF-Token': csrf, Cookie: cookieHeader },
    },
  )
  expect(deleteRes.status()).toBe(204)

  await page.reload()
  await expect(page.getByTestId(`task-card-${created.id}`)).not.toBeVisible({ timeout: 5_000 })
  await expect(page.getByTestId('empty-state')).toBeVisible()
})

test('Scheduled Tasks: create via UI form', async ({ page }) => {
  const wsId = await registerAndLand(page)
  await page.goto(`/w/${wsId}/scheduled-tasks`)

  // Open the dialog
  await page.getByTestId('new-task-button').click()
  await expect(page.getByTestId('task-form-dialog')).toBeVisible()

  // Fill the form
  await page.getByLabel('Name').fill('UI created task')
  await page.getByLabel('Prompt').fill('Run a quick summary of recent news')

  // Schedule kind is already "interval" by default; set interval to 3600
  await page.getByLabel('Interval (seconds)').fill('3600')

  // Submit
  await page.getByRole('button', { name: /create task/i }).click()

  // Dialog closes, task appears
  await expect(page.getByTestId('task-form-dialog')).not.toBeVisible({ timeout: 5_000 })
  await expect(page.getByText('UI created task')).toBeVisible({ timeout: 8_000 })
})

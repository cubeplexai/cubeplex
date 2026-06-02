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

  // Switch to interval frequency and set to 1 hour
  await page.getByRole('button', { name: '每隔…' }).click()
  await page.locator('input[type="number"]').fill('1')

  // Submit
  await page.getByRole('button', { name: /create task/i }).click()

  // Dialog closes, task appears
  await expect(page.getByTestId('task-form-dialog')).not.toBeVisible({ timeout: 5_000 })
  await expect(page.getByText('UI created task')).toBeVisible({ timeout: 8_000 })
})

test('Scheduled Tasks: new-task dialog shows frequency pills, not cron input', async ({ page }) => {
  const wsId = await registerAndLand(page)
  await page.goto(`/w/${wsId}/scheduled-tasks`)

  await page.getByRole('button', { name: /new task/i }).click()
  await expect(page.getByTestId('task-form-dialog')).toBeVisible()

  // Frequency pills visible
  await expect(page.getByRole('button', { name: '每天' })).toBeVisible()
  await expect(page.getByRole('button', { name: '每周' })).toBeVisible()
  await expect(page.getByRole('button', { name: '每月' })).toBeVisible()

  // No raw cron input
  await expect(page.locator('input[placeholder="0 9 * * 1-5"]')).not.toBeVisible()

  // Switch to 每周 and confirm weekday pills appear (exact match so '一'
  // doesn't collide with the '一次' frequency pill that's still in the DOM).
  await page.getByRole('button', { name: '每周' }).click()
  await expect(page.getByRole('button', { name: '一', exact: true })).toBeVisible()

  // Switch to 每月 and confirm day grid appears
  await page.getByRole('button', { name: '每月' }).click()
  await expect(page.getByRole('button', { name: '15' })).toBeVisible()
  await expect(page.getByRole('button', { name: /月末/ })).toBeVisible()
})

test('Scheduled Tasks: create daily task via new UI → API receives 5-field cron', async ({
  page,
  request,
}) => {
  const wsId = await registerAndLand(page)
  const apiBase = await getApiBase(page)
  const { cookieHeader, csrf } = await getCookies(page)

  await page.goto(`/w/${wsId}/scheduled-tasks`)
  await page.getByRole('button', { name: /new task/i }).click()
  await page.getByLabel('Name').fill('Daily E2E')
  await page.getByLabel('Prompt').fill('Say hello')
  // Default is 每天 09:00 — just submit
  await page.getByRole('button', { name: /create task/i }).click()

  // Wait for the UI to confirm the POST landed before querying the API —
  // racing the GET against the POST commit makes this test flake.
  await expect(page.getByTestId('task-form-dialog')).not.toBeVisible({ timeout: 5_000 })
  await expect(page.getByText('Daily E2E')).toBeVisible({ timeout: 8_000 })

  // Verify the created task has a 5-field cron
  const tasks = await request.get(`${apiBase}/api/v1/ws/${wsId}/scheduled-tasks`, {
    headers: { 'X-CSRF-Token': csrf, Cookie: cookieHeader },
  })
  const { tasks: list } = (await tasks.json()) as {
    tasks: Array<{ name: string; cron_expr: string | null }>
  }
  const created = list.find((t) => t.name === 'Daily E2E')
  expect(created).toBeDefined()
  expect(created!.cron_expr?.split(' ')).toHaveLength(5)
})

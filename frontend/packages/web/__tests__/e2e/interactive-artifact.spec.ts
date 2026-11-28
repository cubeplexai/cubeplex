import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { test, expect, type Page } from '@playwright/test'
import { REAL_LLM_TAG, registerAndLand, skipWithoutRealLlm } from './_helpers/auth'

/**
 * An artifact can call the platform model at runtime (artifact call bridge).
 *
 * Both tests open the artifact from the **workspace gallery**, not from the
 * conversation that produced it, because that is how a shared artifact is
 * usually reached. The HTML under test mirrors the snippet the system prompt
 * gives the agent, so this also guards the authoring contract.
 *
 * Artifacts are only created by the agent (`save_artifact`) — there is no HTTP
 * create route — so the fixture seeds rows + object bytes directly through
 * `backend/scripts/seed_interactive_artifact.py`.
 *
 * Running locally requires the backend in the test env (so registration goes
 * through onboarding), with matching DB config for the seeding script:
 *
 *   cd backend && ENV_FOR_DYNACONF=test uv run python main.py
 *   cd frontend && ENV_FOR_DYNACONF=test npx playwright test interactive-artifact
 *
 * The `@real-llm` test needs a model. Without credentials, run the stub:
 *
 *   cd backend && uv run python scripts/fake_openai_sse.py --port 8099
 *   CUBEPLEX_E2E_LLM_BASE_URL=http://127.0.0.1:8099/v1 CUBEPLEX_E2E_REAL_LLM=true \
 *     npx playwright test interactive-artifact --grep @real-llm
 */
// e2e -> __tests__ -> web -> packages -> frontend -> repo root
const BACKEND_DIR = path.resolve(__dirname, '../../../../../backend')
const FIXTURE_HTML = path.resolve(__dirname, 'fixtures/interactive-artifact.html')

interface Seeded {
  conversation_id: string
  artifact_id: string
  workspace_id: string
}

async function seedArtifact(page: Page, wsId: string): Promise<Seeded> {
  const me = await page.request.get('/api/v1/auth/me')
  expect(me.ok()).toBe(true)
  const userId = ((await me.json()) as { id: string }).id

  const stdout = execFileSync(
    'uv',
    [
      'run',
      'python',
      'scripts/seed_interactive_artifact.py',
      '--workspace-id',
      wsId,
      '--user-id',
      userId,
      '--html-file',
      FIXTURE_HTML,
    ],
    { cwd: BACKEND_DIR, encoding: 'utf8', timeout: 120_000 },
  )
  return JSON.parse(stdout.trim().split('\n').pop() ?? '{}') as Seeded
}

/** Open the artifact from the gallery and return its preview frame locator. */
async function openFromGallery(page: Page, wsId: string, artifactId: string) {
  await page.goto(`/w/${wsId}/artifacts`)
  const card = page.getByTestId(`artifact-card-${artifactId}`)
  await expect(card).toBeVisible({ timeout: 15_000 })
  await card.click()

  const frame = page.frameLocator('[data-testid="artifact-preview-slot"] iframe')
  await expect(frame.locator('[data-testid="artifact-ask"]')).toBeVisible({ timeout: 20_000 })
  return frame
}

test('artifact panel reaches the bridge with cookies but no CSRF header', async ({ page }) => {
  const { wsId } = await registerAndLand(page)
  const seeded = await seedArtifact(page, wsId)
  const frame = await openFromGallery(page, wsId, seeded.artifact_id)

  // Discovery: GET from inside the sandboxed iframe, with the real session cookie.
  const functions = await frame.locator('body').evaluate(async () => {
    const m = location.pathname.match(/\/ws\/([^/]+)\/conversations\/([^/]+)\/artifacts\/([^/]+)\//)
    if (!m) return { status: -1, names: [] as string[] }
    const res = await fetch(`/api/v1/ws/${m[1]}/conversations/${m[2]}/artifacts/${m[3]}/functions`)
    const body = await res.json()
    return {
      status: res.status,
      names: (body.functions ?? []).map((f: { name: string }) => f.name),
    }
  })
  expect(functions.status).toBe(200)
  expect(functions.names).toContain('chat')

  // POST without an X-CSRF-Token: 400 proves the request cleared the sandbox,
  // the session cookie, the CSRF exemption, and conversation scoping. A 401,
  // 403 or 404 here would mean the browser contract is broken.
  const invalid = await frame.locator('body').evaluate(async () => {
    const m = location.pathname.match(/\/ws\/([^/]+)\/conversations\/([^/]+)\/artifacts\/([^/]+)\//)
    if (!m) return -1
    const res = await fetch(
      `/api/v1/ws/${m[1]}/conversations/${m[2]}/artifacts/${m[3]}/call/chat`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ args: {} }),
      },
    )
    return res.status
  })
  expect(invalid).toBe(400)
})

test('artifact displays the model answer in the panel', { tag: REAL_LLM_TAG }, async ({ page }) => {
  skipWithoutRealLlm()
  const { wsId } = await registerAndLand(page)
  const seeded = await seedArtifact(page, wsId)
  const frame = await openFromGallery(page, wsId, seeded.artifact_id)

  await frame.locator('[data-testid="artifact-input"]').fill('Reply with the word: pong')
  await frame.locator('[data-testid="artifact-ask"]').click()

  const output = frame.locator('[data-testid="artifact-output"]')
  await expect(output).not.toHaveText('thinking…', { timeout: 60_000 })
  await expect(output).not.toHaveText('idle')
  await expect(output).not.toHaveText(/^(error|threw):/)
  await expect(output).toHaveText(/\w/, { timeout: 60_000 })
})

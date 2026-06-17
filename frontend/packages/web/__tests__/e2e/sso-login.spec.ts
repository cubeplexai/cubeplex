/**
 * SSO login UI smoke tests.
 *
 * Covers button-level behavior on /login and /login/{slug}. Real IdP
 * roundtrip is deferred to Task 15. We mock the backend SSO endpoints so
 * the harness doesn't need a configured OIDC provider.
 */
import { test, expect, type Route } from '@playwright/test'

const SSO_AUTHORIZE_URL = 'https://idp.example.com/authorize?client_id=x&state=y'
const GOOGLE_AUTHORIZE_URL = 'https://accounts.google.com/o/oauth2/v2/auth?client_id=x'

async function mockSystemInfo(page: import('@playwright/test').Page, mode: string): Promise<void> {
  await page.route('**/api/v1/system/info', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        deployment_mode: mode,
        needs_org_setup: false,
        version: 'test',
        sandbox_enabled: false,
      }),
    })
  })
}

async function mockSsoInitiate(page: import('@playwright/test').Page): Promise<void> {
  await page.route('**/api/v1/auth/sso/initiate', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ redirect_url: SSO_AUTHORIZE_URL }),
    })
  })
}

async function mockGoogleAuthorize(
  page: import('@playwright/test').Page,
  status: number,
): Promise<void> {
  await page.route('**/api/v1/auth/social/google/authorize', async (route: Route) => {
    if (status === 200) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ redirect_url: GOOGLE_AUTHORIZE_URL }),
      })
    } else {
      await route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Google login not configured' }),
      })
    }
  })
}

async function mockOrgInfo(
  page: import('@playwright/test').Page,
  slug: string,
  result: { status: 200; body: object } | { status: 404 },
): Promise<void> {
  await page.route(`**/api/v1/auth/org-info/${slug}`, async (route: Route) => {
    if (result.status === 200) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(result.body),
      })
    } else {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Organization not found' }),
      })
    }
  })
}

test.describe('/login — SSO + Google buttons', () => {
  test.beforeEach(async ({ context }) => {
    await context.clearCookies()
  })

  test('multi-tenant: SSO button reveals slug input and redirects on Continue', async ({
    page,
  }) => {
    await mockSystemInfo(page, 'multi_tenant')
    await mockSsoInitiate(page)
    // Stop the navigation to the IdP so the test can assert the target URL.
    await page.route(SSO_AUTHORIZE_URL, (route: Route) =>
      route.fulfill({ status: 200, contentType: 'text/html', body: '<html>idp</html>' }),
    )

    await page.goto('/login')
    await page.getByRole('button', { name: /sso login/i }).click()

    const slugInput = page.getByPlaceholder(/organization identifier/i)
    await expect(slugInput).toBeVisible()
    await slugInput.fill('acme')

    await page.getByRole('button', { name: /^continue$/i }).click()
    await page.waitForURL(SSO_AUTHORIZE_URL, { timeout: 5_000 })
  })

  test('single-tenant: SSO button initiates directly without slug input', async ({ page }) => {
    await mockSystemInfo(page, 'single_tenant')
    await mockSsoInitiate(page)
    await page.route(SSO_AUTHORIZE_URL, (route: Route) =>
      route.fulfill({ status: 200, contentType: 'text/html', body: '<html>idp</html>' }),
    )

    await page.goto('/login')
    await page.getByRole('button', { name: /sso login/i }).click()
    await page.waitForURL(SSO_AUTHORIZE_URL, { timeout: 5_000 })
  })

  test('Google button: 404 shows "not configured" inline', async ({ page }) => {
    await mockSystemInfo(page, 'multi_tenant')
    await mockGoogleAuthorize(page, 404)

    await page.goto('/login')
    await page.getByRole('button', { name: /login with google/i }).click()
    await expect(page.getByText(/google login is not configured/i)).toBeVisible()
  })
})

test.describe('/login/{slug} — org-specific entry', () => {
  test.beforeEach(async ({ context }) => {
    await context.clearCookies()
  })

  test('renders SSO Login button when sso_enabled=true', async ({ page }) => {
    await mockSystemInfo(page, 'multi_tenant')
    await mockOrgInfo(page, 'acme', {
      status: 200,
      body: { org_name: 'Acme Inc.', sso_enabled: true, sso_protocol: 'oidc' },
    })
    await mockSsoInitiate(page)
    await page.route(SSO_AUTHORIZE_URL, (route: Route) =>
      route.fulfill({ status: 200, contentType: 'text/html', body: '<html>idp</html>' }),
    )

    await page.goto('/login/acme')
    await expect(page.getByRole('heading', { name: /login to acme inc\./i })).toBeVisible()
    // Slug already pinned by the URL — click goes directly to the IdP.
    await page.getByRole('button', { name: /sso login/i }).click()
    await page.waitForURL(SSO_AUTHORIZE_URL, { timeout: 5_000 })
  })

  test('renders password form fallback when sso_enabled=false', async ({ page }) => {
    await mockSystemInfo(page, 'multi_tenant')
    await mockOrgInfo(page, 'acme', {
      status: 200,
      body: { org_name: 'Acme Inc.', sso_enabled: false, sso_protocol: null },
    })

    await page.goto('/login/acme')
    await expect(page.getByLabel(/email/i)).toBeVisible()
    await expect(page.getByLabel(/password/i)).toBeVisible()
  })

  test('shows "Organization not found" on 404', async ({ page }) => {
    await mockSystemInfo(page, 'multi_tenant')
    await mockOrgInfo(page, 'no-such-org', { status: 404 })

    await page.goto('/login/no-such-org')
    await expect(page.getByTestId('org-not-found')).toBeVisible()
    await expect(page.getByText(/organization not found/i)).toBeVisible()
  })
})

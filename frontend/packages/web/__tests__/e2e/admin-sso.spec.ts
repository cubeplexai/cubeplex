/**
 * Admin SSO Authentication page UI smoke tests.
 *
 * Covers the empty state, OIDC form rendering, the Discover button wiring,
 * the save -> testing-state transition, activation with confirmation, and
 * the linked identities table + unlink confirmation.
 *
 * We mock all `/api/v1/admin/...` SSO endpoints — exercising a real OIDC
 * IdP roundtrip is deferred to the backend integration tests (Task 15).
 */
import { test, expect, type Page, type Route } from '@playwright/test'
import { registerAndLand } from './_helpers/auth'

const ORG_SLUG = 'acme'

async function mockAdminOrg(page: Page): Promise<void> {
  await page.route('**/api/v1/admin/org', async (route: Route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id: 'org_1', name: 'Acme', slug: ORG_SLUG }),
    })
  })
}

async function mockNoSso(page: Page): Promise<void> {
  await page.route('**/api/v1/admin/sso', async (route: Route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: 'null' })
    } else {
      await route.fallback()
    }
  })
}

const TESTING_CONNECTION = {
  id: 'sso_1',
  org_id: 'org_1',
  protocol: 'oidc',
  display_name: 'Acme Okta',
  status: 'testing',
  provisioning: 'auto',
  config: {
    issuer: 'https://idp.example.com',
    authorization_endpoint: 'https://idp.example.com/authorize',
    token_endpoint: 'https://idp.example.com/token',
    jwks_uri: 'https://idp.example.com/jwks',
    client_id: 'acme-client',
    scopes: ['openid', 'email', 'profile'],
    attribute_mapping: { id: 'sub', email: 'email', name: 'name' },
  },
  created_at: '2026-06-17T00:00:00+00:00',
  updated_at: '2026-06-17T00:00:00+00:00',
}

test.describe('Admin SSO Authentication page', () => {
  test.beforeEach(async ({ page }) => {
    await mockAdminOrg(page)
  })

  test('empty state shows "Configure SSO" and reveals the OIDC form', async ({ page }) => {
    await registerAndLand(page)
    await mockNoSso(page)
    await page.goto('/admin/authentication')

    await expect(page.getByTestId('sso-empty')).toBeVisible({ timeout: 10_000 })
    await page.getByTestId('sso-configure').click()

    await expect(page.getByTestId('sso-oidc-section')).toBeVisible()
    await expect(page.getByTestId('sso-issuer')).toBeVisible()
    await expect(page.getByTestId('sso-discover')).toBeVisible()
  })

  test('Discover button fills OIDC endpoints from the discovery response', async ({ page }) => {
    await registerAndLand(page)
    await mockNoSso(page)
    await page.route('**/api/v1/admin/sso/discover-oidc', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          issuer: 'https://idp.example.com',
          authorization_endpoint: 'https://idp.example.com/authorize',
          token_endpoint: 'https://idp.example.com/token',
          userinfo_endpoint: 'https://idp.example.com/userinfo',
          jwks_uri: 'https://idp.example.com/jwks',
        }),
      })
    })
    await page.goto('/admin/authentication')
    await page.getByTestId('sso-configure').click()

    await page.getByTestId('sso-issuer').fill('https://idp.example.com')
    await page.getByTestId('sso-discover').click()

    await expect(page.getByTestId('sso-field-oidc-authz')).toHaveValue(
      'https://idp.example.com/authorize',
    )
    await expect(page.getByTestId('sso-field-oidc-token')).toHaveValue(
      'https://idp.example.com/token',
    )
    await expect(page.getByTestId('sso-field-oidc-jwks')).toHaveValue(
      'https://idp.example.com/jwks',
    )
  })

  test('save creates connection and transitions to testing-state panel', async ({ page }) => {
    await registerAndLand(page)

    // First GET returns null; once we POST create, subsequent GET would return
    // the connection — but the page state updates from the POST response.
    await mockNoSso(page)
    await page.route('**/api/v1/admin/sso', async (route: Route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify(TESTING_CONNECTION),
        })
      } else {
        await route.fallback()
      }
    })
    await page.route('**/api/v1/admin/sso/sso_1/identities*', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    })

    await page.goto('/admin/authentication')
    await page.getByTestId('sso-configure').click()

    await page.getByTestId('sso-display-name').fill('Acme Okta')
    await page.getByTestId('sso-issuer').fill('https://idp.example.com')
    await page.getByTestId('sso-field-oidc-authz').fill('https://idp.example.com/authorize')
    await page.getByTestId('sso-field-oidc-token').fill('https://idp.example.com/token')
    await page.getByTestId('sso-field-oidc-jwks').fill('https://idp.example.com/jwks')
    await page.getByTestId('sso-client-id').fill('acme-client')
    await page.getByTestId('sso-client-secret').fill('s3cret')

    await page.getByTestId('sso-save').click()

    await expect(page.getByTestId('sso-status-panel')).toBeVisible({ timeout: 10_000 })
    await expect(page.getByTestId('sso-activate')).toBeVisible()
  })

  test('activate flow prompts for confirmation and updates status', async ({ page }) => {
    await registerAndLand(page)

    await page.route('**/api/v1/admin/sso', async (route: Route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(TESTING_CONNECTION),
        })
      } else {
        await route.fallback()
      }
    })
    await page.route('**/api/v1/admin/sso/sso_1/activate', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...TESTING_CONNECTION, status: 'active' }),
      })
    })
    await page.route('**/api/v1/admin/sso/sso_1/identities*', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    })

    await page.goto('/admin/authentication')
    await expect(page.getByTestId('sso-activate')).toBeVisible({ timeout: 10_000 })

    await page.getByTestId('sso-activate').click()
    await expect(page.getByTestId('sso-confirm')).toBeVisible()
    await page.getByTestId('sso-confirm').click()

    await expect(page.getByTestId('sso-deactivate')).toBeVisible({ timeout: 10_000 })
  })

  test('identities list renders rows and unlink confirms before deleting', async ({ page }) => {
    await registerAndLand(page)

    await page.route('**/api/v1/admin/sso', async (route: Route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(TESTING_CONNECTION),
        })
      } else {
        await route.fallback()
      }
    })
    await page.route('**/api/v1/admin/sso/sso_1/identities*', async (route: Route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([
            {
              id: 'eid_1',
              user_id: 'user_1',
              provider_type: 'oidc',
              external_id: 'okta-abc',
              external_email: 'alice@example.com',
              created_at: '2026-06-17T00:00:00+00:00',
            },
          ]),
        })
      } else {
        await route.fallback()
      }
    })
    let unlinkCalled = false
    await page.route('**/api/v1/admin/sso/sso_1/identities/eid_1', async (route: Route) => {
      if (route.request().method() === 'DELETE') {
        unlinkCalled = true
        await route.fulfill({ status: 204, body: '' })
      } else {
        await route.fallback()
      }
    })

    await page.goto('/admin/authentication')
    await expect(page.getByTestId('sso-identities')).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('alice@example.com')).toBeVisible()

    await page.getByTestId('sso-unlink-eid_1').click()
    await expect(page.getByTestId('sso-unlink-confirm')).toBeVisible()
    await page.getByTestId('sso-unlink-confirm').click()

    await expect.poll(() => unlinkCalled, { timeout: 5_000 }).toBe(true)
    await expect(page.getByText('alice@example.com')).toHaveCount(0)
  })
})

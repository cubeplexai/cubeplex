import path from 'node:path'
import { test, expect } from '@playwright/test'
import { gotoAdminSkills, registerAsAdmin } from './_helpers'

const FIXTURE_ZIP = path.resolve(__dirname, '../../fixtures/sample-skill.zip')

test.describe('admin skills upload', () => {
  test('admin uploads a .zip and sees a success toast', async ({ page }) => {
    await registerAsAdmin(page)
    await gotoAdminSkills(page)

    await page.getByRole('button', { name: /上传 skill/ }).click()
    await expect(page.getByTestId('upload-skill-modal')).toBeVisible()

    await page.getByTestId('upload-skill-file-input').setInputFiles(FIXTURE_ZIP)
    await page.getByTestId('upload-skill-submit').click()

    await expect(page.getByTestId('upload-skill-success')).toBeVisible({ timeout: 15_000 })
  })
})

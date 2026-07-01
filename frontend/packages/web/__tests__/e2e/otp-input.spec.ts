import { test, expect } from '@playwright/test'

function uniqueEmail(): string {
  return `otp-${Date.now()}-${Math.random().toString(16).slice(2, 6)}@example.com`
}

// Client state machine for <OtpInput> on the /verify-otp page: paste-fill.
// This is the one OTP behavior the backend can't observe (it's pure client DOM
// state). The OTP send/verify/cooldown/rate-limit invariants are covered by the
// backend e2e suite (test_register_otp_flow.py), so we don't duplicate them
// here — a real OTP round-trip in Playwright would need SMTP or a scraped code.

test('paste fills all 6 cells', async ({ page }) => {
  await page.goto(`/verify-otp?email=${encodeURIComponent(uniqueEmail())}&next=/`)
  const firstCell = page.getByLabel('Digit 1')
  await firstCell.focus()
  await page.evaluate(() => {
    const data = new DataTransfer()
    data.setData('text/plain', '123456')
    document.activeElement?.dispatchEvent(
      new ClipboardEvent('paste', { clipboardData: data, bubbles: true }),
    )
  })
  for (let i = 1; i <= 6; i++) {
    await expect(page.getByLabel(`Digit ${i}`)).toHaveValue(String(i))
  }
})

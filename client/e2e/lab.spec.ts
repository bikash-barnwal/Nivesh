import { expect, type Page, test } from '@playwright/test'

function watchErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
  page.on('console', (m) => m.type() === 'error' && errors.push(`console: ${m.text()}`))
  page.on('response', (r) => r.url().includes('/api/') && r.status() >= 500 && errors.push(`HTTP ${r.status()} ${r.url()}`))
  return errors
}

test('strategies page lists the YAML strategies', async ({ page }) => {
  const errors = watchErrors(page)
  await page.goto('/strategies')
  await expect(page.getByText('Strategy lab')).toBeVisible()
  await expect(page.getByText('Trend Stack v1')).toBeVisible()
  await expect(page.getByText('Breakout + Volume v1')).toBeVisible()
  await expect(page.getByText('Leaders Pullback v1')).toBeVisible()
  expect(errors).toEqual([])
})

test('run a backtest from the UI, open the run, and inspect a trade', async ({ page }) => {
  test.setTimeout(120_000)
  const errors = watchErrors(page)
  await page.goto('/strategies')
  const row = page.getByRole('row', { name: /Trend Stack v1/ })
  await row.getByRole('button', { name: /Run backtest|Re-run/ }).click()
  await expect(row.getByText('Done')).toBeVisible({ timeout: 90_000 })
  await expect(row.getByRole('link', { name: 'Open' })).toBeVisible()
  await row.getByRole('link', { name: 'Open' }).click()

  await expect(page).toHaveURL(/\/runs\//)
  await expect(page.getByText('Performance', { exact: true })).toBeVisible()
  await expect(page.getByText('Max drawdown')).toBeVisible()
  await expect(page.getByText('Anti-overfitting gates')).toBeVisible()
  await expect(page.locator('canvas').first()).toBeVisible()
  await page.screenshot({ path: 'test-results/run.png', fullPage: true })

  const journal = page.getByRole('listbox', { name: 'Trades' })
  await expect(journal.getByRole('option').first()).toBeVisible()
  await journal.getByRole('option').nth(2).click()
  await expect(page.getByText('How it came onto the radar & entry')).toBeVisible()
  await expect(page.getByText('Trail & exit')).toBeVisible()
  await expect(page.getByText(/ranked #\d+ by/)).toBeVisible()
  await page.getByLabel('Sort').selectOption('pnl')
  await expect(journal.getByRole('option').first()).toBeVisible()
  await page.screenshot({ path: 'test-results/trade.png', fullPage: true })
  expect(errors).toEqual([])
})

test('an unknown run shows a friendly message', async ({ page }) => {
  await page.goto('/runs/does-not-exist')
  await expect(page.getByText(/This run does not exist/)).toBeVisible()
})

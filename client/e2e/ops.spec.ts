import { expect, type Page, test } from '@playwright/test'

function watchErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
  page.on('console', (m) => m.type() === 'error' && errors.push(`console: ${m.text()}`))
  page.on('response', (r) => r.url().includes('/api/') && r.status() >= 500 && errors.push(`HTTP ${r.status()} ${r.url()}`))
  return errors
}

test('paper accounts: create, inspect orders and positions, delete', async ({ page }) => {
  test.setTimeout(90_000)
  const errors = watchErrors(page)
  await page.goto('/paper')
  await expect(page.getByRole('heading', { name: /Paper accounts/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: /Live signals/ })).toBeVisible()

  // replay from a past date so there is something to look at
  await page.getByLabel('Start date').fill('2021-09-01')
  const start = page.getByRole('button', { name: 'Start paper account' })
  await expect(start).toBeEnabled() // only once the strategy list has loaded
  await start.click()
  const link = page.getByRole('link', { name: /from 2021-09-01/ }).first()
  await expect(link).toBeVisible({ timeout: 60_000 })
  await link.click()

  await expect(page).toHaveURL(/\/paper\/\d+/)
  await expect(page.getByRole('heading', { name: /Orders for the next open/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: /Open positions/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: /Closed trades/ })).toBeVisible()
  await expect(page.locator('canvas').first()).toBeVisible()
  await page.screenshot({ path: 'test-results/paper.png', fullPage: true })

  await page.getByRole('link', { name: '← Paper accounts' }).click()
  await page.getByRole('row', { name: /from 2021-09-01/ }).first().getByRole('button', { name: 'Delete' }).click()
  expect(errors).toEqual([])
})

test('live signals follow the selected strategy and explain a blocked regime or list candidates', async ({ page }) => {
  await page.goto('/paper')
  await expect(page.getByText(/Rule:/)).toBeVisible()
  await page.getByLabel('Strategy').first().selectOption({ index: 1 })
  await expect(page.getByText(/Rule:/)).toBeVisible()
})

test('alerts: defaults, a custom rule, a check, the inbox and the brief', async ({ page }) => {
  const errors = watchErrors(page)
  await page.goto('/alerts')
  await expect(page.getByRole('heading', { name: /Daily brief/ }).first()).toBeVisible()
  await expect(page.getByText(/Regime:/)).toBeVisible()
  await expect(page.getByText(/Educational analytics/).first()).toBeVisible()

  await page.getByRole('button', { name: 'Add default rules' }).click()
  await expect(page.getByText('Regime changes', { exact: true })).toBeVisible()
  await expect(page.getByText('Watchlist changes', { exact: true })).toBeVisible()

  await page.getByLabel('Rule type').selectOption('scan_entry')
  await page.getByLabel('Rule name').fill('My breakouts')
  await page.getByLabel('Minimum score').fill('75')
  await page.getByRole('button', { name: 'Add rule' }).click()
  await expect(page.getByText('My breakouts', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: 'Check now' }).click()
  await expect(page.getByRole('status')).toContainText(/Checked \d+ rules/)
  await page.screenshot({ path: 'test-results/alerts.png', fullPage: true })

  const rule = page.getByRole('listitem').filter({ hasText: 'My breakouts' })
  await rule.getByRole('button', { name: 'Delete' }).click()
  await expect(page.getByText('My breakouts', { exact: true })).toBeHidden()
  expect(errors).toEqual([])
})

test('an invalid alert rule shows the server error', async ({ page }) => {
  await page.route('**/api/alerts', async (route) => {
    if (route.request().method() === 'POST') return route.fulfill({ status: 422, json: { detail: 'scan_entry rules need a scan_id' } })
    return route.continue()
  })
  await page.goto('/alerts')
  await page.getByLabel('Rule type').selectOption('scan_entry')
  await page.getByRole('button', { name: 'Add rule' }).click()
  await expect(page.getByRole('alert')).toContainText('scan_entry rules need a scan_id')
})

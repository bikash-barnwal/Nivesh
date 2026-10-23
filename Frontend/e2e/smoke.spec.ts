import { expect, type Page, test } from '@playwright/test'

/** Collects anything the browser would show in devtools as an error. */
function watchErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
  page.on('console', (m) => m.type() === 'error' && errors.push(`console: ${m.text()}`))
  page.on('response', (r) => r.url().includes('/api/') && r.status() >= 500 && errors.push(`HTTP ${r.status()} ${r.url()}`))
  return errors
}

const ROUTES: { path: string; expects: RegExp; shot: string }[] = [
  { path: '/market', expects: /Intelligence engines/i, shot: 'market' },
  { path: '/sectors', expects: /Group leaderboard/i, shot: 'sectors' },
  { path: '/stocks/S001', expects: /Stage 1/i, shot: 'stock' },
  { path: '/scans/breakout_volume', expects: /Rule:/i, shot: 'scans' },
  { path: '/watchlist', expects: /Watchlist/i, shot: 'watchlist' },
]

for (const route of ROUTES) {
  test(`${route.path} renders without errors`, async ({ page }) => {
    const errors = watchErrors(page)
    await page.goto(route.path)
    await expect(page.getByText(route.expects).first()).toBeVisible()
    await page.waitForLoadState('networkidle')
    await page.screenshot({ path: `test-results/${route.shot}.png`, fullPage: true })
    expect(errors).toEqual([])
  })
}

test('/ redirects to the market view and keeps the disclaimer', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/\/market/)
  await expect(page.getByText(/not investment advice/i)).toBeVisible()
})

test('market view shows regime, nine engines and similar markets', async ({ page }) => {
  await page.goto('/market')
  await expect(page.getByText(/Early Bull|Bear|Correction|Recovery|Neutral|Late Bull|Broad Bull|Momentum Bull/).first()).toBeVisible()
  await expect(page.getByRole('button', { name: /Buying Pressure/ })).toBeVisible()
  await expect(page.locator('button', { hasText: /Pressure|Leadership|Participation|Volatility|Breadth|Momentum Quality|Liquidity|Exhaustion/ })).toHaveCount(9)
  await expect(page.getByText(/Similar past markets/i)).toBeVisible()
})

test('clicking a regime cell time-travels via ?as_of= and shows the historical badge', async ({ page }) => {
  const errors = watchErrors(page)
  await page.goto('/market')
  const cells = page.getByRole('list', { name: 'Regime history' }).getByRole('listitem')
  await cells.nth(60).click()
  await expect(page).toHaveURL(/as_of=\d{4}-\d{2}-\d{2}/)
  await expect(page.getByText('Historical read')).toBeVisible()
  await expect(page.getByRole('button', { name: /Buying Pressure/ })).toBeVisible()
  await page.getByRole('button', { name: 'Latest' }).click()
  await expect(page).not.toHaveURL(/as_of=/)
  await expect(page.getByText('Historical read')).toBeHidden()
  expect(errors).toEqual([])
})

test('as_of survives navigation between pages', async ({ page }) => {
  await page.goto('/market?as_of=2021-06-01')
  await page.getByRole('link', { name: 'Sectors' }).click()
  await expect(page).toHaveURL(/\/sectors\?as_of=2021-06-01/)
  await expect(page.getByText(/Group leaderboard/i)).toBeVisible()
})

test('an engine card opens its evidence drawer and Escape closes it', async ({ page }) => {
  await page.goto('/market')
  await page.getByRole('button', { name: /Participation/ }).click()
  const drawer = page.getByRole('dialog')
  await expect(drawer).toBeVisible()
  await expect(drawer.getByText(/How it is computed/i)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(drawer).toBeHidden()
})

test('stock page draws the chart, safety checklist and lifecycle', async ({ page }) => {
  const errors = watchErrors(page)
  await page.goto('/stocks/S001')
  await expect(page.locator('canvas').first()).toBeVisible()
  await expect(page.getByText(/Stage 1/)).toBeVisible()
  await expect(page.getByText(/Momentum lifecycle/i)).toBeVisible()
  await expect(page.getByText('Circuit streak').first()).toBeVisible()
  expect(errors).toEqual([])
})

test('stock search navigates to the chosen symbol', async ({ page }) => {
  await page.goto('/market')
  await page.getByLabel('Stock lookup').fill('S01')
  await page.getByRole('button', { name: /^S01\d/ }).first().click()
  await expect(page).toHaveURL(/\/stocks\/S01\d/)
})

test('watchlist toggle persists and can be removed', async ({ page }) => {
  await page.goto('/stocks/S005')
  await page.getByRole('button', { name: /Watch$/ }).click()
  await expect(page.getByRole('button', { name: /Watching/ })).toBeVisible()
  await page.goto('/watchlist')
  await expect(page.getByRole('link', { name: 'S005' })).toBeVisible()
  await page.getByRole('button', { name: 'Remove' }).first().click()
  await expect(page.getByRole('link', { name: 'S005' })).toBeHidden()
})

test('unknown symbol shows a friendly message, not a crash', async ({ page }) => {
  const errors = watchErrors(page)
  await page.goto('/stocks/NOSUCH')
  await expect(page.getByText(/No data for NOSUCH/)).toBeVisible()
  expect(errors.filter((e) => !e.includes('404'))).toEqual([])
})

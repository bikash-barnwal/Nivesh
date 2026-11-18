import { expect, type Page, test } from '@playwright/test'

const env = (data: unknown) => ({
  as_of: null,
  data_through: '2022-09-06',
  version: 1,
  data,
  provenance: { source: 'test', calculation: 'mock' },
})

const EVENTS = [
  {
    source_id: 'n1', symbol: 'RELIANCE', event_type: 'order_win', direction: 'positive', event_date: '2022-09-05',
    published_at: '2022-09-05T10:00:00', available_from: '2022-09-06', confidence: 0.85,
    summary: 'Wins a large order', value_cr: 1234.5, pct_of_revenue: 0.02, magnitude_grounded: true, url: 'https://example.com/n1',
  },
  {
    source_id: 'n2', symbol: 'TCS', event_type: 'fraud', direction: 'negative', event_date: '2022-09-04',
    published_at: '2022-09-04T10:00:00', available_from: '2022-09-05', confidence: 0.6,
    summary: 'Probe announced', value_cr: null, pct_of_revenue: null, magnitude_grounded: false, url: null,
  },
]

const STUDY = [
  {
    event_type: 'order_win', direction: 'positive', n: 12,
    mean_excess_1d: 0.01, mean_excess_5d: 0.02, mean_excess_20d: -0.03,
    hit_rate_1d: 0.6, hit_rate_5d: 0.7, hit_rate_20d: 0.4,
    t_stat_1d: 1.1, t_stat_5d: 2.2, t_stat_20d: -1.3,
  },
]

async function mockNews(page: Page) {
  await page.route('**/api/news/events**', (route) => route.fulfill({ json: env(EVENTS) }))
  await page.route('**/api/news/study', (route) => route.fulfill({ json: env(STUDY) }))
  await page.route('**/api/news/status', (route) =>
    route.fulfill({ json: env({ items: 40, extracted: { done: 38, failed: 2 }, events_by_type: { order_win: 5, fraud: 1 } }) }),
  )
}

test('news page renders the study, events and safe source links', async ({ page }) => {
  await mockNews(page)
  await page.goto('/news')

  await expect(page.getByRole('heading', { name: 'News' })).toBeVisible()
  await expect(page.getByRole('status')).toContainText('40 items ingested')
  await expect(page.getByRole('cell', { name: 'Order Win' })).toBeVisible()
  await expect(page.getByRole('cell', { name: '+2.00%' })).toBeVisible()
  await expect(page.getByText('Wins a large order')).toBeVisible()
  await expect(page.getByText('Amount ₹1,234.5 cr')).toBeVisible()
  await expect(page.getByText('Probe announced')).toBeVisible()
  await expect(page.getByText('Amount')).toHaveCount(1)

  const link = page.getByRole('link', { name: 'Source' })
  await expect(link).toHaveCount(1)
  await expect(link).toHaveAttribute('href', 'https://example.com/n1')
  await expect(link).toHaveAttribute('target', '_blank')
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
})

test('news page tells the user how to build the data when nothing exists', async ({ page }) => {
  await page.route('**/api/news/**', (route) => route.fulfill({ status: 404, json: { detail: 'not built' } }))
  await page.goto('/news')
  await expect(page.getByText('nivesh news-ingest').first()).toBeVisible()
})

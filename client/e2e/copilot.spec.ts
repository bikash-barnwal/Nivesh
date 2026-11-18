import { expect, type Page, test } from '@playwright/test'

/** The copilot UI is tested against a mocked NDJSON stream: no model keys, fully deterministic. */
const ndjson = (events: object[]) => events.map((e) => JSON.stringify(e)).join('\n') + '\n'

const EVIDENCE = {
  evidence_id: 'ev_abc12345',
  kind: 'analytical',
  question: 'q',
  data_through: '2022-09-06',
  executed_at: '2026-10-08T05:00:00+00:00',
  sql: "SELECT symbol, close FROM stock_daily WHERE date = DATE '2022-09-06' LIMIT 2",
  columns: ['symbol', 'close'],
  rows: [['S000', 123.45], ['S001', 233.11]],
  row_count: 2,
  truncated: false,
  source_views: ['stock_daily'],
  calculation: 'closing prices for two symbols',
  heal_log: [],
  confidence: 'deterministic',
}

const GOOD = [
  { type: 'status', stage: 'checking dates' },
  { type: 'status', stage: 'routing' },
  { type: 'route', intent: 'analytical', reason: 'needs numbers', model: 'gemini/flash' },
  { type: 'sql', attempt: 1, sql: EVIDENCE.sql, model: 'openrouter/kimi' },
  { type: 'heartbeat' },
  { type: 'evidence', evidence: EVIDENCE },
  { type: 'answer', markdown: '[FACT] S001 closed at **233.11**.\n\n| symbol | close |\n|---|---|\n| S000 | 123.45 |\n| S001 | 233.11 |', grounded: true, mode: 'model', ungrounded: [] },
  { type: 'done', elapsed_s: 1.2, models: { router: 'gemini/flash', sql: 'openrouter/kimi', synth: 'gemini/flash' } },
]

async function mockAsk(page: Page, events: object[]) {
  await page.route('**/api/copilot/ask', (route) => route.fulfill({ status: 200, contentType: 'application/x-ndjson', body: ndjson(events) }))
}

async function mockModels(page: Page, keyPresent: boolean) {
  await page.route('**/api/copilot/models', (route) =>
    route.fulfill({
      json: {
        as_of: null, data_through: null, version: 1, provenance: { source: 'x', calculation: 'y' },
        data: { roles: [{ role: 'router', position: 'primary', provider: 'gemini', model: 'flash', local: false, key_present: keyPresent, key_env: 'GEMINI_API_KEY' }] },
      },
    }),
  )
}

test('a streamed answer renders markdown, a verified badge and the evidence behind it', async ({ page }) => {
  await mockModels(page, true)
  await mockAsk(page, GOOD)
  await page.goto('/copilot')
  await page.getByLabel('Question').fill('closing prices on the latest date')
  await page.getByRole('button', { name: 'Ask' }).click()

  await expect(page.getByText('Numbers verified against evidence')).toBeVisible()
  await expect(page.locator('strong', { hasText: '233.11' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'S000' }).first()).toBeVisible()
  await page.screenshot({ path: 'test-results/copilot.png', fullPage: true })

  // Evidence panel: SQL, Result, JSON, Freshness
  await expect(page.getByText('Attempt 1 · openrouter/kimi')).toBeVisible()
  await expect(page.getByText(/SELECT symbol, close FROM stock_daily/)).toBeVisible()
  await page.getByRole('tab', { name: 'Result' }).click()
  await expect(page.getByRole('columnheader', { name: 'close' }).last()).toBeVisible() // evidence table, not the answer table
  await page.getByRole('tab', { name: 'JSON' }).click()
  await expect(page.getByText('"evidence_id": "ev_abc12345"')).toBeVisible()
  await page.getByRole('tab', { name: 'Freshness' }).click()
  await expect(page.getByText('2022-09-06').first()).toBeVisible()
  await expect(page.getByText('openrouter/kimi')).toBeVisible()
})

test('a rejected model answer is flagged and the raw result is shown instead', async ({ page }) => {
  await mockModels(page, true)
  await mockAsk(page, [
    ...GOOD.slice(0, 6),
    { type: 'answer', markdown: '**Result** (2 rows)\n\n| symbol | close |\n|---|---|\n| S000 | 123.45 |', grounded: false, mode: 'deterministic', ungrounded: ['1,234,567.89'] },
    GOOD[7]!,
  ])
  await page.goto('/copilot')
  await page.getByLabel('Question').fill('prices')
  await page.getByRole('button', { name: 'Ask' }).click()
  await expect(page.getByText('Model wording rejected — raw result shown')).toBeVisible()
  await expect(page.getByText('Numbers verified against evidence')).toBeHidden()
})

test('a failed request shows the error, and the input is usable again', async ({ page }) => {
  await mockModels(page, true)
  await mockAsk(page, [{ type: 'status', stage: 'routing' }, { type: 'error', message: "provider 'gemini' needs an API key: set GEMINI_API_KEY" }])
  await page.goto('/copilot')
  await page.getByLabel('Question').fill('prices')
  await page.getByRole('button', { name: 'Ask' }).click()
  await expect(page.getByRole('alert')).toContainText('GEMINI_API_KEY')
  await expect(page.getByLabel('Question')).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Stop' })).toBeHidden()
})

test('a missing API key is explained before the first question', async ({ page }) => {
  await mockModels(page, false)
  await page.goto('/copilot')
  await expect(page.getByText(/No API key found for: GEMINI_API_KEY/)).toBeVisible()
})

test('example questions can be clicked and an empty question cannot be sent', async ({ page }) => {
  await mockModels(page, true)
  await mockAsk(page, GOOD)
  await page.goto('/copilot')
  await expect(page.getByRole('button', { name: 'Ask' })).toBeDisabled()
  await page.getByRole('button', { name: /How many stocks are above their 200-day average/ }).click()
  await expect(page.getByText('Numbers verified against evidence')).toBeVisible()
})

test('a refusal is shown as a plain answer', async ({ page }) => {
  await mockModels(page, true)
  await mockAsk(page, [
    { type: 'status', stage: 'checking dates' },
    { type: 'evidence', evidence: { evidence_id: 'ev_ref', kind: 'refusal', question: 'q', data_through: '2022-09-06', executed_at: 'now', reason: 'The data ends on 2022-09-06.' } },
    { type: 'answer', markdown: 'The data ends on 2022-09-06, so I cannot answer about 2099-01-01 (after the last session).', grounded: true, mode: 'deterministic', ungrounded: [] },
    { type: 'done', elapsed_s: 0, models: {} },
  ])
  await page.goto('/copilot')
  await page.getByLabel('Question').fill('close on 2099-01-01')
  await page.getByRole('button', { name: 'Ask' }).click()
  await expect(page.getByText(/after the last session/)).toBeVisible()
  await expect(page.getByText('Rendered directly from evidence')).toBeVisible()
})

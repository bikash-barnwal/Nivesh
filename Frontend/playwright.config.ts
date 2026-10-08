import { defineConfig } from '@playwright/test'

// The smoke tests run against synthetic data (`uv run nivesh demo-data`), never the real NSE dataset:
//   cd Backend && uv run nivesh demo-data     # once
//   cd Frontend && npx playwright test
const demoDir = process.env.NIVESH_DEMO_DIR ?? '../data/demo'

export default defineConfig({
  testDir: './e2e',
  outputDir: 'test-results',
  timeout: 30_000,
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:15001', viewport: { width: 1440, height: 1000 } },
  webServer: [
    {
      command: 'uv run nivesh serve --port 15000',
      cwd: '../Backend',
      env: { NIVESH_DATA_DIR: demoDir },
      url: 'http://127.0.0.1:15000/health',
      reuseExistingServer: true,
      timeout: 60_000,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1',
      url: 'http://127.0.0.1:15001',
      reuseExistingServer: true,
      timeout: 60_000,
    },
  ],
})

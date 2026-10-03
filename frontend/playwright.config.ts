import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  use: { baseURL: 'http://127.0.0.1:4179' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 4179 --strictPort',
    url: 'http://127.0.0.1:4179/mission-control/',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})

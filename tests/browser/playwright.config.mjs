import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { defineConfig } from '@playwright/test';

const browserTests = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(browserTests, '../..');
// Parallel worktrees each serve their own fixture site.
const port = Number(process.env.PW_PORT || 4173);

export default defineConfig({
  testDir: browserTests,
  testMatch: ['dashboard-smoke.spec.mjs', 'v2-*.spec.mjs'],
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  timeout: 45_000,
  expect: { timeout: 25_000 },
  reporter: process.env.CI ? [['line'], ['html', { open: 'never' }]] : 'list',
  outputDir: resolve(browserTests, 'test-results'),
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    browserName: 'chromium',
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `python3 -m http.server ${port} --bind 127.0.0.1 --directory _site`,
    cwd: repoRoot,
    url: `http://127.0.0.1:${port}/index.html`,
    reuseExistingServer: !process.env.CI,
    timeout: 15_000,
  },
});

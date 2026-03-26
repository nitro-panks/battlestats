import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.PLAYWRIGHT_EXTERNAL_BASE_URL || 'http://127.0.0.1:3100';
const useExternalBaseUrl = Boolean(process.env.PLAYWRIGHT_EXTERNAL_BASE_URL) || process.env.PLAYWRIGHT_DISABLE_WEBSERVER === '1';

export default defineConfig({
    testDir: './e2e',
    fullyParallel: false,
    reporter: process.env.CI
        ? [['list'], ['html', { open: 'never' }]]
        : [['list']],
    retries: process.env.CI ? 2 : 0,
    outputDir: 'test-results/playwright',
    use: {
        baseURL,
        trace: 'retain-on-failure',
        screenshot: 'only-on-failure',
        video: 'retain-on-failure',
    },
    projects: [
        {
            name: 'chromium',
            use: {
                ...devices['Desktop Chrome'],
            },
        },
    ],
    webServer: useExternalBaseUrl ? undefined : {
        command: 'npm run dev -- --hostname 127.0.0.1 --port 3100',
        url: baseURL,
        reuseExistingServer: !process.env.CI,
        timeout: 120000,
    },
});
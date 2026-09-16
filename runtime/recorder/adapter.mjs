import { createRequire } from 'node:module';
import { chromium } from 'playwright';

const require = createRequire(import.meta.url);
export const PLAYWRIGHT_VERSION = '1.63.0';

/** Only private API dependency. Matches upstream codegen(), pinned and integration-tested. */
export async function enableCodegen(context, outputFile) {
  const actual = require('playwright/package.json').version;
  if (actual !== PLAYWRIGHT_VERSION || typeof context._enableRecorder !== 'function')
    throw new Error(`Unsupported recorder adapter: expected Playwright ${PLAYWRIGHT_VERSION}`);
  await context._enableRecorder({
    language: 'playwright-test', mode: 'recording', outputFile,
    launchOptions: { headless: false }, contextOptions: {},
  });
}

export async function launchRecorder(input) {
  const role = input.environment.roles?.find(item => item.name === input.role);
  const browser = await chromium.launch({
    headless: false,
    args: ['--window-position=0,0', '--window-size=1024,950', '--disable-quic', '--proxy-bypass-list=<-loopback>'],
    ...(input.proxy ? { proxy: { server: input.proxy, bypass: '' } } : {}),
  });
  const context = await browser.newContext({
    viewport: { width: 1000, height: 800 }, serviceWorkers: 'block',
    ...(role?.storageState ? { storageState: role.storageState } : {}),
    ...(role?.headers ? { extraHTTPHeaders: role.headers } : {}),
  });
  const origins = new Set([
    ...Object.values(input.environment.websites || {}),
    ...Object.values(input.environment.apiBases || {}),
    ...(input.environment.allowedOrigins || []),
  ].map(value => new URL(value).origin));
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return origins.has(url.origin) ? route.continue() : route.abort('blockedbyclient');
  });
  await enableCodegen(context, '/work/recording.spec.ts');
  const page = await context.newPage();
  await page.goto(input.url, { timeout: 45_000 });
  return { browser, context, page };
}

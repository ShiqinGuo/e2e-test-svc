import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';

const [base, fixture, evidence] = process.argv.slice(2);
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const owner = await browser.newContext({ baseURL: base });
const other = await browser.newContext({ baseURL: base });
const anonymous = await browser.newContext({ baseURL: base });
const api = async (context, method, url, data) => {
  const response = await context.request.fetch(url, { method, data });
  assert.ok(response.ok(), `${method} ${url} returned ${response.status()}`);
  return response.json();
};
let recordingUrl;
try {
  for (const [name, context] of [['Owner', owner], ['Other', other]])
    await api(context, 'POST', '/api/auth/sign-up/email', { name, email: `${randomUUID()}@example.com`, password: `probe-${randomUUID()}` });
  const organization = await api(owner, 'POST', '/api/v1/organizations', { name: 'Real noVNC proxy probe' });
  const project = await api(owner, 'POST', '/api/v1/projects', { name: 'Real noVNC proxy probe', workspaceId: organization.defaultWorkspaceId });
  const root = `/api/v1/projects/${project.id}`;
  const env = await api(owner, 'POST', `${root}/environments`, { name: 'Isolated fixture', websites: { main: fixture }, apiBases: {} });
  const recording = await api(owner, 'POST', `${root}/recordings`, { environmentId: env.id, website: 'main' });
  recordingUrl = `${root}/recordings/${recording.id}`;
  let current;
  for (let i = 0; i < 100; i++) {
    current = await api(owner, 'GET', recordingUrl);
    if (current.status !== 'starting') break;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  assert.equal(current.status, 'ready', current.error || 'Recorder did not become ready');
  const page = await owner.newPage();
  let browserLeakedGatewayHeader = false;
  page.on('request', request => { if (request.headers()['x-recorder-token']) browserLeakedGatewayHeader = true; });
  await page.goto(current.viewerUrl);
  await page.getByText('已连接 · 在 Inspector 选择断言，再点选页面目标', { exact: true }).waitFor({ timeout: 20_000 });
  assert.ok(await page.locator('#screen canvas').count() >= 1);
  assert.equal(browserLeakedGatewayHeader, false);
  await page.screenshot({ path: path.join(evidence, 'python-proxy-desktop.png'), fullPage: true });
  assert.equal((await anonymous.request.get(current.viewerUrl)).status(), 401);
  assert.equal((await other.request.get(current.viewerUrl)).status(), 404);
  const otherPage = await other.newPage();
  await otherPage.goto('/api/health');
  const unauthorizedWs = await otherPage.evaluate(url => new Promise(resolve => {
    const socket = new WebSocket(url.replace(/^http/, 'ws').replace('/index.html', '/websockify'), ['binary']);
    socket.onopen = () => { socket.close(); resolve('opened'); };
    socket.onerror = () => resolve('denied');
  }), base + current.viewerUrl);
  assert.equal(unauthorizedWs, 'denied');
  await api(owner, 'POST', '/api/auth/sign-out', {});
  await page.getByText('录制桌面连接已断开', { exact: true }).waitFor({ timeout: 10_000 });
  const result = { realPythonApi: true, realDockerRecorder: true, browser: 'Microsoft Edge headless', cookieAuthenticatedNoVnc: true, noGatewayTokenInBrowserRequests: true, anonymousHttp: 401, otherAccountHttp: 404, otherAccountWebsocket: 'denied', logoutClosesWebsocket: true };
  await writeFile(path.join(evidence, 'python-proxy-result.json'), JSON.stringify(result, null, 2));
  process.stdout.write(JSON.stringify(result) + '\n');
} finally {
  await browser.close();
}

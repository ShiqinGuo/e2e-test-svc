// Isolated technical fixture. Tests upstream UI; these test-only hooks are never used by the API.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { randomBytes } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';
import { launchRecorder } from './adapter.mjs';
import { createRecorderGateway } from './gateway.mjs';

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function poll(fn, message) { for (let i = 0; i < 100; i++) { if (await fn()) return; await wait(100); } throw new Error(message); }
let submissions = 0;
const fixture = createServer((request, response) => {
  if (request.url === '/submit') { submissions++; response.end('saved'); return; }
  response.setHeader('Content-Type', 'text/html');
  response.end(`<!doctype html><title>Isolated recorder fixture</title><style>body{font:22px sans-serif;padding:60px}button,input{font:22px sans-serif;margin:15px;padding:16px}</style><h1>Recorder technical fixture</h1><form><label>Order <input aria-label="Order" value="draft-42"></label><button type="submit">Submit order</button></form><output>No submission</output><script>document.querySelector('form').onsubmit=async e=>{e.preventDefault();await fetch('/submit',{method:'POST'});document.querySelector('output').textContent='Saved';}</script>`);
});
await new Promise(resolve => fixture.listen(8888, '127.0.0.1', resolve));
const url = 'http://127.0.0.1:8888';
const recorder = await launchRecorder({ url, environment: { websites: { main: url }, roles: [] } });
const impl = recorder.context._connection.toImpl(recorder.context);
const inspectorBrowser = await chromium.connectOverCDP(impl.recorderAppForTest.wsEndpointForTest);
const inspector = inspectorBrowser.contexts()[0].pages()[0];
const code = async () => readFile('/work/recording.spec.ts', 'utf8').catch(() => '');
const clickTarget = async locator => {
  const box = await locator.boundingBox();
  assert.ok(box);
  await recorder.page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await wait(120);
  await recorder.page.mouse.down(); await recorder.page.mouse.up();
};
try {
  await inspector.getByTitle('Assert visibility', { exact: true }).click();
  await wait(350);
  await clickTarget(recorder.page.getByRole('button', { name: 'Submit order' }));
  await poll(async () => (await code()).includes('toBeVisible'), 'Visibility assertion was not recorded');
  assert.equal(submissions, 0, 'Assert visibility must consume submit click');
  await inspector.getByTitle('Assert text', { exact: true }).click();
  await wait(350);
  await clickTarget(recorder.page.getByRole('button', { name: 'Submit order' }));
  await wait(200);
  const accept = recorder.page.getByRole('button', { name: 'Assert', exact: true });
  if (await accept.count()) await accept.click();
  else await recorder.page.getByTitle('Accept', { exact: true }).click();
  await poll(async () => /toContainText|toHaveText/.test(await code()), 'Text assertion was not recorded');
  assert.equal(submissions, 0, 'Assert text must consume submit click');
  await inspector.getByTitle('Assert value', { exact: true }).click();
  await wait(350);
  await clickTarget(recorder.page.getByRole('textbox', { name: 'Order', exact: true }));
  await poll(async () => (await code()).includes('toHaveValue'), 'Value assertion was not recorded');
  assert.equal(submissions, 0);
  await clickTarget(recorder.page.getByRole('button', { name: 'Submit order' }));
  await poll(() => submissions === 1, 'Normal record mode must perform one actual submit');
  await poll(async () => (await code()).includes('.click()'), 'Actual submit action was not recorded');
  const token = randomBytes(32).toString('hex');
  const gateway = createRecorderGateway({ token, status: () => ({ ready: true }), stop: async () => {} });
  await new Promise(resolve => gateway.listen(6080, '127.0.0.1', resolve));
  assert.equal((await fetch('http://127.0.0.1:6080/')).status, 401);
  assert.equal((await fetch('http://127.0.0.1:6080/', { headers: { 'x-recorder-token': 'wrong' } })).status, 401);
  const viewerBrowser = await chromium.launch({ headless: true });
  const viewer = await viewerBrowser.newPage({ extraHTTPHeaders: { 'x-recorder-token': token }, viewport: { width: 1600, height: 1045 } });
  await viewer.goto('http://127.0.0.1:6080/index.html');
  await viewer.getByText('已连接 · 在 Inspector 选择断言，再点选页面目标', { exact: true }).waitFor();
  await wait(1500);
  assert.equal(await viewer.locator('canvas').count(), 1);
  await viewer.screenshot({ path: '/work/novnc-desktop.png', fullPage: true });
  await viewerBrowser.close(); gateway.close();
  const result = { playwright: '1.63.0', novnc: '1.6.0', visibilityWithoutSubmit: true, textWithoutSubmit: true, value: true, actualSubmitCount: submissions, noVncBrowserConnected: true, unauthorizedGatewayStatus: 401 };
  await writeFile('/work/adapter-result.json', JSON.stringify(result, null, 2));
  process.stdout.write(JSON.stringify(result) + '\n');
} finally {
  await recorder.context.close(); await recorder.browser.close(); await inspectorBrowser.close(); fixture.close();
}

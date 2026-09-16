import assert from 'node:assert/strict';
import test from 'node:test';
import { bindRecordingCode } from '../src/runtime/recorder.js';
import { readFile } from 'node:fs/promises';

test('recorded navigation binds named environment variables without editing assertions/comments', () => {
  const code = `import {test,expect} from '@playwright/test';
test('one', async ({page}) => {
  // page.goto('https://old.example/ignored');
  await page.goto('https://old.example/shop');
  await page.goto('https://admin.example/orders?status=draft');
  await expect(page.getByText('https://old.example/shop')).toBeVisible();
  await expect(page.getByRole('textbox')).toHaveValue('draft-42');
});`;
  const output = bindRecordingCode(code, { main: 'https://old.example/shop', admin: 'https://admin.example/' });
  assert.ok(output.code.includes('page.goto(process.env["E2E_WEBSITE_main"]!)'));
  assert.ok(output.code.includes('new URL("/orders?status=draft", process.env["E2E_WEBSITE_admin"]!).href'));
  assert.ok(output.code.includes("// page.goto('https://old.example/ignored')"));
  assert.ok(output.code.includes("getByText('https://old.example/shop')"));
  assert.equal(output.checks.length, 2);
  assert.equal(output.checks[1].expected, "'draft-42'");
});

test('unknown recorded navigation is rejected instead of silently binding wrong environment', () => {
  assert.throws(() => bindRecordingCode(`await page.goto('https://other.example/');`, { main: 'https://selected.example/' }), /未命名/);
});

test('actions alone remain without checks', () => {
  const result = bindRecordingCode(`await page.goto('https://test.example/'); await page.getByRole('button').click();`, { main: 'https://test.example/' });
  assert.deepEqual(result.checks, []);
});

test('real Inspector evidence is exactly the code rebound for two-environment runner verification', async () => {
  const raw = await readFile(new URL('../runtime/recorder/evidence/recording.spec.ts', import.meta.url), 'utf8');
  const expected = await readFile(new URL('../runtime/recorder/evidence/bound-recording.spec.ts', import.meta.url), 'utf8');
  const bound = bindRecordingCode(raw, { main: 'http://127.0.0.1:8888/' });
  assert.equal(bound.code, expected);
  assert.equal(bound.checks.length, 3);
});

test('known secret values use runtime bindings while check descriptions do not expose the value', () => {
  const output = bindRecordingCode(`await page.getByLabel('Password').fill('synthetic-secret'); await expect(page.getByLabel('Password')).toHaveValue('synthetic-secret');`, {}, { secretVariables: { password: 'synthetic-secret' } });
  assert.equal(output.code.includes('synthetic-secret'), false);
  assert.ok(output.code.includes('process.env["E2E_VAR_password"]!'));
  assert.equal(output.checks[0].expected, "'[secret:password]'");
});

test('long assertion metadata stays within API limits without changing the full assertion code', () => {
  const locatorText = '订单📦'.repeat(100);
  const expectedText = '完整预期📦'.repeat(1000);
  const statement = `expect(page.getByText(${JSON.stringify(locatorText)})).toHaveText(${JSON.stringify(expectedText)})`;
  const code = `await ${statement};`;
  const output = bindRecordingCode(code, {});
  assert.equal(output.code, code);
  assert.equal(output.checks.length, 1);
  assert.equal(Array.from(output.checks[0].title).length, 200);
  assert.equal(Array.from(output.checks[0].expected!).length, 4000);
  assert.equal(output.checks[0].title, Array.from(statement).slice(0, 200).join(''));
  assert.equal(output.checks[0].expected, Array.from(JSON.stringify(expectedText)).slice(0, 4000).join(''));
});

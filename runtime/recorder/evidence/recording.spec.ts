import { test, expect } from '@playwright/test';

test('test', async ({ page }) => {
  await page.goto('http://127.0.0.1:8888/');
  await expect(page.getByRole('button', { name: 'Submit order' })).toBeVisible();
  await expect(page.getByRole('button')).toContainText('Submit order');
  await expect(page.getByRole('textbox', { name: 'Order' })).toHaveValue('draft-42');
  await page.getByRole('button', { name: 'Submit order' }).click();
});
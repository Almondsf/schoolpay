import { expect, test } from '@playwright/test';

import { signIn } from './helpers';

test('bursar generates the term invoices in one click and corrects one', async ({ page }) => {
  await signIn(page, 'bursar@demo.schoolpay.ng', { mfa: true });
  await page.getByRole('link', { name: 'Billing' }).click();
  await page.getByRole('button', { name: 'Generate invoices' }).click();
  await page.getByRole('button', { name: 'Preview' }).click();
  await expect(page.getByText(/Preview: \d+ invoices totalling/)).toBeVisible();
  await page.getByRole('button', { name: /^Generate \d+ invoices$/ }).click();
  await expect(page.getByText(/Done: \d+ invoices/)).toBeVisible();
  await page.getByRole('button', { name: 'Close' }).click();

  const first = page.getByRole('row').nth(1);
  const student = (await first.getByRole('link').nth(1).textContent())!;
  await first.getByRole('link').first().click();
  await expect(page.getByRole('heading', { name: /Invoice #\d+/ })).toBeVisible();
  await page.getByRole('button', { name: 'Correct amount' }).click();
  await page.getByLabel('Description').fill('PTA levy waived');
  await page.getByLabel('Amount (₦)').fill('-5000');
  await page.getByLabel('Reason (kept with the invoice)').fill('Approved by proprietor');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('cell', { name: /PTA levy waived/ })).toBeVisible();

  await page.getByRole('link', { name: student }).click();
  await expect(page.getByRole('heading', { name: 'Fees' })).toBeVisible();
  await expect(page.getByText('Owed')).toBeVisible();
});

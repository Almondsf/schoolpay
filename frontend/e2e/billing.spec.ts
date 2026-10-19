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

  // The parent will pay the rest in instalments
  await page.getByRole('button', { name: 'Pay in instalments' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Instalments').selectOption('2');
  await dialog.getByLabel('Note (optional)').fill('Agreed with the mother at the bursary');
  await dialog.getByRole('button', { name: 'Agree plan' }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText(/instalment\s+1 of 2/)).toBeVisible();
  await expect(page.getByText('Agreed with the mother at the bursary')).toBeVisible();

  await page.getByRole('link', { name: student }).click();
  await expect(page.getByRole('heading', { name: 'Fees' })).toBeVisible();
  await expect(page.getByText('Owed')).toBeVisible();
});

test('a fee is added to SSS 3 after billing, with one pupil left out', async ({ page }) => {
  await signIn(page, 'bursar@demo.schoolpay.ng', { mfa: true });
  await page.getByRole('link', { name: 'Billing' }).click();
  await page.getByRole('button', { name: 'Add a fee to classes' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel("New fee's name").fill(`WAEC ${Date.now() % 100000}`);
  await dialog.getByLabel('Amount per pupil (₦)').fill('25000');
  await dialog.getByLabel('SSS 3', { exact: true }).check();
  await dialog.getByRole('button', { name: 'Preview' }).click();
  await expect(dialog.getByText(/pupils will pay WAEC/)).toBeVisible();
  await dialog.locator('.student-ticks input[type=checkbox]').first().uncheck();
  await dialog.getByRole('button', { name: /^Add to \d+ pupils$/ }).click();
  await expect(dialog.getByText(/added\./)).toBeVisible();
  await dialog.getByRole('button', { name: 'Close' }).click();
});

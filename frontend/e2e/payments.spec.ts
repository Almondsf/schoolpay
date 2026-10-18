import { expect, test } from '@playwright/test';

import { signIn } from './helpers';

test('a transfer into a student account number is matched automatically', async ({ page }) => {
  await signIn(page, 'bursar@demo.schoolpay.ng', { mfa: true });
  await page.getByRole('link', { name: 'Students' }).click();
  await page.getByRole('row').nth(1).getByRole('link').first().click();
  const accountNumber = (await page.locator('.account-number').textContent())!.trim();
  expect(accountNumber).toMatch(/^\d{10}$/);

  // A parent pays ₦25,000 from their bank app (the fake gateway sends the signed webhook)
  const sent = await page.request.post('/api/v1/dev/fake-gateway/transfers', {
    data: { account_number: accountNumber, amount_kobo: 2_500_000 },
  });
  expect(sent.ok()).toBeTruthy();

  await page.reload();
  await expect(page.getByRole('cell', { name: 'Virtual account' })).toBeVisible();
  await page.getByRole('link', { name: 'Payments' }).click();
  await expect(page.getByRole('cell', { name: /₦25,000\.00/ }).first()).toBeVisible();
});

test('cash recorded by the admin is approved by the bursar', async ({ browser }) => {
  const admin = await browser.newPage();
  await signIn(admin, 'admin@demo.schoolpay.ng');
  await admin.getByRole('link', { name: 'Payments' }).click();
  await admin.getByRole('button', { name: 'Record cash or teller' }).click();
  await admin.getByLabel('Find student').fill('DMC');
  await admin.getByRole('listbox').getByRole('option').first().click();
  await admin.getByLabel('Amount (₦)').fill('12,345');
  await admin.getByRole('button', { name: 'Record', exact: true }).click();
  await expect(admin.getByRole('cell', { name: /₦12,345\.00/ })).toBeVisible();
  await expect(admin.getByRole('button', { name: 'Approve' })).toHaveCount(0); // admins record, they don't approve

  const bursar = await browser.newPage();
  await signIn(bursar, 'bursar@demo.schoolpay.ng', { mfa: true });
  await bursar.getByRole('link', { name: 'Payments' }).click();
  await bursar.getByRole('tab', { name: 'Awaiting approval' }).click();
  const row = bursar.getByRole('row', { name: /₦12,345\.00/ });
  await row.getByRole('button', { name: 'Approve' }).click();
  await expect(row).toHaveCount(0);
  await bursar.getByRole('tab', { name: 'Received' }).click();
  await expect(bursar.getByRole('cell', { name: '₦12,345.00', exact: true })).toBeVisible();
});

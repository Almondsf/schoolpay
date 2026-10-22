import { expect, test } from '@playwright/test';

import { signIn } from './helpers';

test('the platform admin bills the term and records a school paying', async ({ page }) => {
  await signIn(page, 'platform@demo.wardpay.ng', { mfa: true, landing: 'Platform overview' });
  const nav = page.getByRole('navigation', { name: 'Main' });

  await nav.getByRole('link', { name: 'Billing', exact: true }).click();
  await page.getByRole('button', { name: 'Bill this term' }).click();
  await page.getByRole('button', { name: 'Preview' }).click();
  await expect(page.getByText(/Preview: \d+ bills totalling/)).toBeVisible();
  const send = page.getByRole('button', { name: /^Send \d+ bills$/ });
  if (await send.isEnabled()) {
    await send.click();
    await expect(page.getByText(/Done: \d+ bills/)).toBeVisible();
  }
  await page.getByRole('button', { name: /Close|Cancel/ }).click();

  const row = page.getByRole('row', { name: /Demo Model College/ }).first();
  await row.getByRole('button', { name: 'Record payment' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Amount (₦)').fill('10000');
  await dialog.getByLabel('Bank reference (optional)').fill('NIP/E2E');
  await dialog.getByRole('button', { name: 'Record payment' }).click();
  await expect(dialog).toBeHidden();

  // The school's page shows the plan and what it owes
  await row.getByRole('link', { name: 'Demo Model College' }).click();
  await expect(page.getByRole('heading', { name: 'Demo Model College' })).toBeVisible();
  await expect(page.getByLabel('Plan')).toBeVisible();

  await nav.getByRole('link', { name: 'Plans', exact: true }).click();
  await page.getByRole('row', { name: /Standard/ }).getByRole('button', { name: 'Edit' }).click();
  await page.getByLabel(/Per pupil/).fill('1800');
  await expect(page.getByText('Billed ₦600.00 per pupil each term.')).toBeVisible();
  await page.getByRole('button', { name: 'Save plan' }).click();
  await expect(page.getByRole('cell', { name: /₦600\.00/ })).toBeVisible();
});

test('a school signs itself up', async ({ page }) => {
  await page.goto('/console/login');
  await page.getByRole('link', { name: 'New school? Sign up' }).click();
  await page.getByLabel('School name').fill(`E2E Academy ${Date.now()}`);
  await page.getByLabel('Your name (proprietor)').fill('Mrs Test Proprietor');
  await page.getByLabel(/^Your email/).fill(`owner-${Date.now()}@example.com`);
  await page.getByLabel('Phone').fill('0803 123 4567');
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Sign up' }).click();
  await expect(page.getByRole('heading', { name: 'Check your email' })).toBeVisible();
});

import { expect, test, type Page } from '@playwright/test';

import { MFA_SECRET as E2E_MFA_SECRET, PASSWORD, totp, navigate } from './helpers';

async function signIn(page: Page, email: string) {
  await page.goto('/console/login');
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
}

test('school admin signs in, sees staff, and the session survives a reload', async ({ page }) => {
  await signIn(page, 'admin@demo.wardpay.ng');
  await page.getByLabel('Code').fill(totp(E2E_MFA_SECRET)); // admins can move money: two-factor, like the bursar
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('heading', { name: /Welcome, Ada/ })).toBeVisible();
  await navigate(page, 'Staff');
  await expect(page.getByText('musa bello', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Invite staff' })).toHaveCount(0);
  await page.reload(); // access token is in memory only; the refresh cookie restores the session
  await expect(page.getByRole('heading', { name: 'Staff' })).toBeVisible();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in to WardPay' })).toBeVisible();
});

test('proprietor passes two-factor sign-in and invites a bursar', async ({ page }) => {
  await signIn(page, 'proprietor@demo.wardpay.ng');
  await page.getByLabel('Code').fill(totp(E2E_MFA_SECRET));
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('heading', { name: /Welcome, Grace/ })).toBeVisible();

  await navigate(page, 'Staff');
  await page.getByRole('button', { name: 'Invite staff' }).click();
  const email = `bursar-${Date.now()}@demo.wardpay.ng`;
  await page.getByLabel('Full name').fill('Tunde Bakare');
  await page.getByLabel('Email').fill(email);
  await page.getByRole('button', { name: 'Send invite' }).click();
  await expect(page.getByText(email)).toBeVisible();

  await navigate(page, 'Activity log');
  await expect(page.getByText('Staff invited').first()).toBeVisible();
});

test('wrong password shows an error', async ({ page }) => {
  await page.goto('/console/login');
  await page.getByLabel('Email').fill('admin@demo.wardpay.ng');
  await page.getByLabel('Password').fill('not-the-password');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('alert')).toHaveText('Incorrect email or password.');
});

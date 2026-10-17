import { expect, test, type Page } from '@playwright/test';
import { createHmac } from 'node:crypto';

import { E2E_MFA_SECRET } from '../playwright.config';

const PASSWORD = 'demo-password-123';

/** RFC 6238 TOTP for the seeded secret, so tests can pass two-factor sign-in. */
function totp(secretBase32: string, at = Date.now()): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const char of secretBase32) bits += alphabet.indexOf(char).toString(2).padStart(5, '0');
  const key = Buffer.from(bits.match(/.{8}/g)!.map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 30_000)));
  const hmac = createHmac('sha1', key).update(counter).digest();
  const offset = hmac[hmac.length - 1]! & 0xf;
  const code = (hmac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return code.toString().padStart(6, '0');
}

async function signIn(page: Page, email: string) {
  await page.goto('/console/login');
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
}

test('school admin signs in, sees staff, and the session survives a reload', async ({ page }) => {
  await signIn(page, 'admin@demo.schoolpay.ng');
  await expect(page.getByRole('heading', { name: /Welcome, Ada/ })).toBeVisible();
  await page.getByRole('link', { name: 'Staff' }).click();
  await expect(page.getByText('musa bello', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Invite staff' })).toHaveCount(0);
  await page.reload(); // access token is in memory only; the refresh cookie restores the session
  await expect(page.getByRole('heading', { name: 'Staff' })).toBeVisible();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in to SchoolPay' })).toBeVisible();
});

test('proprietor passes two-factor sign-in and invites a bursar', async ({ page }) => {
  await signIn(page, 'proprietor@demo.schoolpay.ng');
  await page.getByLabel('Code').fill(totp(E2E_MFA_SECRET));
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('heading', { name: /Welcome, Grace/ })).toBeVisible();

  await page.getByRole('link', { name: 'Staff' }).click();
  await page.getByRole('button', { name: 'Invite staff' }).click();
  const email = `bursar-${Date.now()}@demo.schoolpay.ng`;
  await page.getByLabel('Full name').fill('Tunde Bakare');
  await page.getByLabel('Email').fill(email);
  await page.getByRole('button', { name: 'Send invite' }).click();
  await expect(page.getByText(email)).toBeVisible();

  await page.getByRole('link', { name: 'Activity log' }).click();
  await expect(page.getByText('Staff invited').first()).toBeVisible();
});

test('wrong password shows an error', async ({ page }) => {
  await page.goto('/console/login');
  await page.getByLabel('Email').fill('admin@demo.schoolpay.ng');
  await page.getByLabel('Password').fill('not-the-password');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('alert')).toHaveText('Incorrect email or password.');
});

import { createHmac } from 'node:crypto';

import { expect, type Page } from '@playwright/test';

export const PASSWORD = 'demo-password-123';
export const MFA_SECRET = 'JBSWY3DPEHPK3PXP';

/** RFC 6238 TOTP for the seeded secret, so tests can pass two-factor sign-in. */
export function totp(secretBase32: string = MFA_SECRET, at = Date.now()): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const char of secretBase32) bits += alphabet.indexOf(char).toString(2).padStart(5, '0');
  const key = Buffer.from(bits.match(/.{8}/g)!.map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at / 30_000)));
  const hmac = createHmac('sha1', key).update(counter).digest();
  const offset = hmac[hmac.length - 1]! & 0xf;
  return ((hmac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).toString().padStart(6, '0');
}

export async function signIn(page: Page, email: string, { mfa = false } = {}) {
  await page.goto('/console/login');
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  if (mfa) {
    await page.getByLabel('Code').fill(totp());
    await page.getByRole('button', { name: 'Continue' }).click();
  }
  await expect(page.getByRole('heading', { name: /Welcome/ })).toBeVisible();
}

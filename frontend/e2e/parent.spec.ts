import { expect, test } from '@playwright/test';

// The first family in the demo school: two children share this parent's number
const PHONE = '08035000000';

test('a parent signs in with a texted code and pays into a child account', async ({ page }) => {
  await page.goto('/parent');
  await page.getByLabel('Phone number').fill(PHONE);
  await page.getByRole('button', { name: 'Text me a code' }).click();
  await expect(page.getByText(/we've texted it a 6-digit code/)).toBeVisible();

  const latest = await page.request.get(`/api/v1/dev/sms/latest?to=${PHONE}`);
  const code = /\b(\d{6})\b/.exec((await latest.json()).text)![1]!;
  await page.getByLabel('Code').fill(code);
  await page.getByRole('button', { name: 'Sign in' }).click();

  await expect(page.getByRole('heading', { name: /^Hello,/ })).toBeVisible();
  const cards = page.locator('.child-card');
  await expect(cards).toHaveCount(2);

  // Pay ₦10,000 into the first child's own account number (the fake gateway plays the bank)
  const accountNumber = (await cards.first().locator('.account-number').textContent())!.trim();
  await page.request.post('/api/v1/dev/fake-gateway/transfers', { data: { account_number: accountNumber, amount_kobo: 1_000_000 } });

  await cards.first().getByRole('link', { name: 'Details' }).click();
  await expect(page.getByText('₦10,000.00').first()).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: /^Receipt #\d+$/ }).first().click()]);
  expect(download.suggestedFilename()).toMatch(/^receipt-\d+\.pdf$/);

  // The session survives a reload (refresh cookie), and signing out ends it
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Bills' })).toBeVisible();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByLabel('Phone number')).toBeVisible();
});

test('the portal is usable on a small phone screen', async ({ browser }) => {
  const phone = await browser.newPage({ viewport: { width: 360, height: 740 } });
  await phone.goto('/parent/login');
  const box = await phone.getByRole('button', { name: 'Text me a code' }).boundingBox();
  expect(box!.width).toBeGreaterThan(250); // full-width tap target
  const overflow = await phone.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});

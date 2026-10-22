import { expect, test } from '@playwright/test';

const PHONE = '08035000000';

test('the website shows the real prices and the privacy notice', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /School fees, collected and reconciled/ })).toBeVisible();
  const pricing = page.getByRole('region', { name: 'Pricing' });
  await expect(pricing.getByText(/per pupil, per term/)).toBeVisible();
  await expect(pricing.getByText(/₦[\d,]+\.\d\d/).first()).toBeVisible();
  await page.getByRole('link', { name: 'Read our privacy notice' }).click();
  await expect(page.getByRole('heading', { name: 'Privacy notice' })).toBeVisible();
  await expect(page.getByText(/data processor/)).toBeVisible();
});

test('a parent downloads their data', async ({ page }) => {
  await page.goto('/parent/login');
  await page.getByLabel('Phone number').fill(PHONE);
  await page.getByRole('button', { name: 'Text me a code' }).click();
  await expect(page.getByText(/we've texted it a 6-digit code/)).toBeVisible();
  const latest = await page.request.get(`/api/v1/dev/sms/latest?to=${PHONE}`);
  await page.getByLabel('Code').fill(/\b(\d{6})\b/.exec((await latest.json()).text)![1]!);
  await page.getByRole('button', { name: 'Sign in' }).click();

  await page.getByRole('link', { name: 'Settings' }).click();
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Download my data' }).click()]);
  expect(download.suggestedFilename()).toBe('my-data.json');
  const data = JSON.parse(await (await download.createReadStream()).toArray().then((chunks) => Buffer.concat(chunks).toString()));
  expect(data.children.length).toBe(2);
  expect(data.you.phone).toBe('+2348035000000');
});

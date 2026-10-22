import { expect, test } from '@playwright/test';

import { signIn } from './helpers';

const PHONE = '08035000000';

test('the bursar words a reminder, sends one, and sees it in the log', async ({ page }) => {
  await signIn(page, 'bursar@demo.schoolpay.ng', { mfa: true });
  await page.getByRole('link', { name: 'Messages', exact: true }).click();
  await page.getByRole('tab', { name: 'Wording' }).click();

  const reminder = page.getByRole('form', { name: 'Reminder' });
  const box = reminder.getByLabel('Message');
  await box.fill('Hello {parent_name}, {student_name} still owes {balance}{due_phrase}. {wrong}');
  await reminder.getByRole('button', { name: 'Preview' }).click();
  await expect(reminder.getByText('Unknown placeholder: {wrong}.')).toBeVisible();

  await box.fill('Hello {parent_name}, {student_name} still owes {balance}{due_phrase}. {pay_to}');
  await reminder.getByRole('button', { name: 'Preview' }).click();
  await expect(reminder.getByRole('status', { name: 'Preview' })).toContainText('Hello Mrs Ngozi Okafor, Chiamaka Okafor still owes ₦55,000.00');
  await reminder.getByRole('button', { name: 'Save' }).click();
  await expect(reminder.getByText('Your wording')).toBeVisible();

  await page.getByRole('tab', { name: 'Reminder schedule' }).click();
  await page.getByLabel('Days after the due date, while still owed').fill('5, 20');
  await page.getByRole('button', { name: 'Save schedule' }).click();
  await expect(page.getByText('Saved.')).toBeVisible();

  // A student far down the list (other tests pay into the first family's accounts)
  await page.getByRole('link', { name: 'Students', exact: true }).click();
  await page.locator('table tbody tr').last().getByRole('link').first().click();
  // Something to remind about, whether or not this term's bills have been generated yet
  await page.getByRole('tab', { name: 'Fees' }).click();
  await page.getByRole('button', { name: 'Add a charge' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel("What it's for").fill('Excursion to the National Museum');
  await dialog.getByLabel('Price (₦)').fill('7500');
  await dialog.getByRole('button', { name: 'Add charge' }).click();
  await expect(dialog).toBeHidden();
  await page.getByRole('button', { name: 'Send reminder' }).click();
  await expect(page.getByText(/A reminder is on its way/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Reminder sent' })).toBeDisabled();

  await page.getByRole('link', { name: 'Messages', exact: true }).click();
  const row = page.locator('table tbody tr').first();
  await expect(row).toContainText('Reminder');
  // Sent at once by day; held until morning during quiet hours
  await expect(row.locator('.badge')).toHaveText(/Sent|Queued/);
});

test('a parent turns reminders off and back on', async ({ page }) => {
  await page.goto('/parent/login');
  await page.getByLabel('Phone number').fill(PHONE);
  await page.getByRole('button', { name: 'Text me a code' }).click();
  await expect(page.getByText(/we've texted it a 6-digit code/)).toBeVisible();
  const latest = await page.request.get(`/api/v1/dev/sms/latest?to=${PHONE}`);
  await page.getByLabel('Code').fill(/\b(\d{6})\b/.exec((await latest.json()).text)![1]!);
  await page.getByRole('button', { name: 'Sign in' }).click();

  await page.getByRole('link', { name: 'Settings' }).click();
  const toggle = page.getByLabel('Send me new bills and payment reminders');
  await expect(toggle).toBeChecked();
  await toggle.click(); // the box follows the saved setting, so it changes once the server answers
  await expect(toggle).not.toBeChecked();
  await page.reload();
  await expect(page.getByLabel('Send me new bills and payment reminders')).not.toBeChecked();
  await page.getByLabel('Send me new bills and payment reminders').click();
  await expect(page.getByLabel('Send me new bills and payment reminders')).toBeChecked();
});

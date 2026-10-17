import { expect, test, type Page } from '@playwright/test';

async function signInAsAdmin(page: Page) {
  await page.goto('/console/login');
  await page.getByLabel('Email').fill('admin@demo.schoolpay.ng');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: /Welcome/ })).toBeVisible();
}

test('school admin imports students from a spreadsheet', async ({ page }) => {
  await signInAsAdmin(page);
  await page.getByRole('link', { name: 'Students' }).click();
  await expect(page.getByText(/60 active students/)).toBeVisible();
  await page.getByRole('link', { name: 'Import from spreadsheet' }).click();

  const stamp = Date.now();
  const csv = [
    'Adm No,Surname,First Name,Class,Parent Name,Parent Phone',
    `E2E/${stamp}/1,Okoro,Ifeanyi,JSS 1A,Mrs Okoro,0809 111 ${String(stamp).slice(-4)}`,
    `E2E/${stamp}/2,Okoro,Nneka,JSS 2 B,Mrs Okoro,0809 111 ${String(stamp).slice(-4)}`,
    `E2E/${stamp}/3,Bad,Row,JSS 9,,`,
  ].join('\n');
  await page.getByLabel('Spreadsheet').setInputFiles({ name: 'students.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) });
  await page.getByLabel(/Parents agreed/).check();
  await page.getByRole('button', { name: 'Check file' }).click();
  await expect(page.getByText(/Row 4: Class 'JSS 9' doesn't exist/)).toBeVisible();
  await expect(page.getByRole('cell', { name: 'Ifeanyi Okoro' })).toBeVisible();
  await page.getByRole('button', { name: 'Import 2 students' }).click();
  await expect(page.getByText(/2 students added/)).toBeVisible();

  await page.getByRole('link', { name: 'See students' }).click();
  await page.getByPlaceholder('Search name or admission number').fill('Ifeanyi');
  await page.getByRole('link', { name: 'Ifeanyi Okoro' }).click();
  await expect(page.getByText('Mrs Okoro')).toBeVisible();
  await expect(page.getByText('JSS 1 A').first()).toBeVisible();
});

test('classes and calendar are set up', async ({ page }) => {
  await signInAsAdmin(page);
  await page.getByRole('link', { name: 'Classes' }).click();
  await expect(page.getByRole('cell', { name: 'SSS 3', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Calendar' }).click();
  await expect(page.getByRole('heading', { name: '2026/2027' })).toBeVisible();
  await expect(page.getByRole('row', { name: /First term Current/ })).toBeVisible();
});

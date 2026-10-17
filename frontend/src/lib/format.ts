/** "school_admin" -> "School admin", "auth.login_failed" -> "Auth login failed" */
export function humanize(value: string): string {
  const words = value.replace(/[._]/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const dateTime = new Intl.DateTimeFormat('en-NG', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Africa/Lagos' });
const date = new Intl.DateTimeFormat('en-NG', { dateStyle: 'medium', timeZone: 'Africa/Lagos' });

export function formatDateTime(iso: string | null | undefined): string {
  return iso ? dateTime.format(new Date(iso)) : '—';
}

export function formatDate(iso: string | null | undefined): string {
  return iso ? date.format(new Date(iso)) : '—';
}

const naira = new Intl.NumberFormat('en-NG', { style: 'currency', currency: 'NGN', minimumFractionDigits: 2 });

/** Amounts are integer kobo everywhere; format only at the edge. */
export function formatKobo(kobo: number): string {
  return naira.format(kobo / 100);
}

export const ROLE_LABELS: Record<string, string> = {
  proprietor: 'Proprietor',
  bursar: 'Bursar',
  school_admin: 'School admin',
  platform_admin: 'Platform admin',
  parent: 'Parent',
};

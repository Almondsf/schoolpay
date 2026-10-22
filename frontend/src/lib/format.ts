/** "school_admin" -> "School admin", "auth.login_failed" -> "Auth login failed" */
export function humanize(value: string): string {
  const words = value.replace(/[._]/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const dateTime = new Intl.DateTimeFormat('en-NG', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Africa/Lagos' });
const date = new Intl.DateTimeFormat('en-NG', { dateStyle: 'medium', timeZone: 'Africa/Lagos' });

const isoDay = new Intl.DateTimeFormat('en-CA', { timeZone: 'Africa/Lagos' }); // en-CA writes YYYY-MM-DD

/** Today in Lagos as YYYY-MM-DD: the date the server checks against, whatever the device's clock zone. */
export function lagosToday(now: Date = new Date()): string {
  return isoDay.format(now);
}

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

/** How each kind of invoice is named on screen. */
export const INVOICE_KIND_LABELS: Record<string, string> = {
  term: 'Term fees',
  opening_balance: 'Brought forward',
  adhoc: 'One-off charge',
};

export const ROLE_LABELS: Record<string, string> = {
  proprietor: 'Proprietor',
  bursar: 'Bursar',
  school_admin: 'School admin',
  platform_admin: 'Platform admin',
  parent: 'Parent',
};

/**
 * "150,000.50" / "₦150000" / "-2,000" -> kobo as an integer, or null if it isn't a valid amount.
 * Parsed as text (never via floats), so 0.1 + 0.2 problems can't creep into money.
 */
export function parseNairaToKobo(input: string, { allowNegative = false } = {}): number | null {
  const cleaned = input.replace(/[₦,\s]/g, '');
  const match = /^(-)?(\d{1,12})(?:\.(\d{1,2}))?$/.exec(cleaned);
  if (!match) return null;
  const [, minus, naira, kobo = ''] = match;
  if (minus && !allowNegative) return null;
  const value = Number(naira) * 100 + Number(kobo.padEnd(2, '0'));
  return minus ? -value : value;
}

/** kobo -> "150000.50" for editable inputs (no currency sign or separators). */
export function koboToInput(kobo: number): string {
  const sign = kobo < 0 ? '-' : '';
  const abs = Math.abs(kobo);
  const naira = Math.floor(abs / 100);
  const rest = abs % 100;
  return `${sign}${naira}${rest ? `.${String(rest).padStart(2, '0')}` : ''}`;
}

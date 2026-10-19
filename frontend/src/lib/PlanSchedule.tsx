import type { Schemas } from '../api/client';
import { formatDate, formatKobo } from './format';

type Plan = Schemas['PaymentPlanOut'];
type Status = Plan['instalments'][number]['status'];

const STATUS: Record<Status, [string, string]> = {
  paid: ['Paid', 'ok'],
  part_paid: ['Part paid', 'warn'],
  overdue: ['Overdue', 'danger'],
  due_today: ['Due today', 'warn'],
  upcoming: ['Upcoming', ''],
};

/** A payment plan's instalments and how far payments have covered them (staff and parents). */
export function PlanSchedule({ plan }: { plan: Plan }) {
  return (
    <>
      <p className="small">
        {plan.next_due ? (
          <>
            Next: <strong>{formatKobo(plan.next_amount_kobo)}</strong> by <strong>{formatDate(plan.next_due)}</strong> (instalment{' '}
            {plan.next_number} of {plan.instalments.length}).
            {plan.behind_kobo > 0 && <span className="error-text"> {formatKobo(plan.behind_kobo)} is overdue.</span>}
          </>
        ) : (
          <strong>Every instalment is paid.</strong>
        )}
      </p>
      <table className="compact">
        <thead>
          <tr>
            <th>#</th>
            <th>Due</th>
            <th className="num">Amount</th>
            <th className="num">Paid</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {plan.instalments.map((i) => (
            <tr key={i.number}>
              <td>{i.number}</td>
              <td className="nowrap">{formatDate(i.due_date)}</td>
              <td className="num">{formatKobo(i.amount_kobo)}</td>
              <td className="num">{formatKobo(i.paid_kobo)}</td>
              <td>
                <span className={`badge ${STATUS[i.status][1]}`}>{STATUS[i.status][0]}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

/** Evenly split `total` kobo into `count` amounts; the last one takes the leftover kobo. */
export function splitEvenly(total: number, count: number): number[] {
  const each = Math.floor(total / count / 100) * 100; // whole naira per instalment
  return Array.from({ length: count }, (_, n) => (n === count - 1 ? total - each * (count - 1) : each));
}

/** Dates `count` steps apart from `first` (YYYY-MM-DD). Months keep the day, capped at the month's end. */
export function stepDates(first: string, count: number, every: 'month' | 'fortnight' | 'week'): string[] {
  const [y, m, d] = first.split('-').map(Number) as [number, number, number];
  return Array.from({ length: count }, (_, n) => {
    if (every === 'month') {
      const lastDay = new Date(Date.UTC(y, m - 1 + n + 1, 0)).getUTCDate();
      return new Date(Date.UTC(y, m - 1 + n, Math.min(d, lastDay))).toISOString().slice(0, 10);
    }
    const days = every === 'week' ? 7 : 14;
    return new Date(Date.UTC(y, m - 1, d + n * days)).toISOString().slice(0, 10);
  });
}

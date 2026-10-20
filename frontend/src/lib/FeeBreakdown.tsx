import type { Schemas } from '../api/client';
import { formatKobo } from './format';

type FeeLine = Schemas['FeeLineOut'];

const TONE: Record<FeeLine['status'], string> = { Paid: 'ok', 'Part paid': 'warn', Unpaid: 'danger' };

/** What a child's balance is made of, for parents: each fee and what's left on it. */
export function FeeList({ fees, earlierKobo }: { fees: FeeLine[]; earlierKobo: number }) {
  if (fees.length === 0 && earlierKobo <= 0) return null;
  return (
    <ul className="fee-list" aria-label="Fees">
      {fees.map((f) => (
        <li key={f.name} className={f.status === 'Paid' ? 'paid' : ''}>
          <span>{f.name}</span>
          <span className="fee-amount">
            {formatKobo(f.billed_kobo)}
            <span className="small muted">
              {f.status === 'Paid' ? ' · paid' : f.status === 'Part paid' ? ` · ${formatKobo(f.left_kobo)} left` : ''}
            </span>
          </span>
        </li>
      ))}
      {earlierKobo > 0 && (
        <li>
          <span>From earlier terms</span>
          <span className="fee-amount">{formatKobo(earlierKobo)}</span>
        </li>
      )}
    </ul>
  );
}

/** The same breakdown for staff: charged, paid and left on each fee, then earlier debts and the total. */
export function FeeTable({ breakdown }: { breakdown: Schemas['FeeBreakdownOut'] }) {
  const { fees } = breakdown;
  if (fees.length === 0 && breakdown.earlier_left_kobo <= 0) return <p className="muted small">No fees charged this term.</p>;
  const sum = (pick: (f: FeeLine) => number) => fees.reduce((n, f) => n + pick(f), 0);
  return (
    <div className="table-scroll">
      <table aria-label="This term’s fees">
        <thead>
          <tr>
            <th>Fee</th>
            <th className="num">Charged</th>
            <th className="num">Paid</th>
            <th className="num">Left</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {fees.map((f) => (
            <tr key={f.name}>
              <td>{f.name}</td>
              <td className="num">{formatKobo(f.billed_kobo)}</td>
              <td className="num">{formatKobo(f.paid_kobo)}</td>
              <td className="num">{formatKobo(f.left_kobo)}</td>
              <td>
                <span className={`badge ${TONE[f.status]}`}>{f.status}</span>
              </td>
            </tr>
          ))}
          {breakdown.earlier_left_kobo > 0 && (
            <tr>
              <td>From earlier terms</td>
              <td />
              <td />
              <td className="num">{formatKobo(breakdown.earlier_left_kobo)}</td>
              <td>
                <span className="badge danger">Owed</span>
              </td>
            </tr>
          )}
        </tbody>
        <tfoot>
          <tr>
            <th>Total</th>
            <td className="num">{formatKobo(sum((f) => f.billed_kobo))}</td>
            <td className="num">{formatKobo(sum((f) => f.paid_kobo))}</td>
            <td className="num">
              <strong>{formatKobo(breakdown.total_left_kobo)}</strong>
            </td>
            <td />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

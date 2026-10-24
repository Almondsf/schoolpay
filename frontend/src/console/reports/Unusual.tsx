import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { api, unwrap } from '../../api/client';
import { formatDate, formatKobo } from '../../lib/format';
import { Empty, ErrorAlert, Loading } from '../../lib/ui';

export function useUnusualActivity(days: number, enabled = true) {
  return useQuery({
    queryKey: ['unusual-activity', days],
    staleTime: 0,
    enabled,
    queryFn: async () => unwrap(await api.GET('/api/v1/reports/unusual-activity', { params: { query: { days } } })),
  });
}

/** Where school money usually goes missing, for the proprietor to look at. Nothing here accuses anyone. */
export function Unusual() {
  const [days, setDays] = useState(30);
  const found = useUnusualActivity(days);
  return (
    <>
      <p className="muted small">
        Things worth a look, because they’re where school money usually goes missing. Each says what happened and who recorded it, so you
        can ask. Most will have a good reason.
      </p>
      <div className="filters">
        <label className="inline-field">
          Over the last{' '}
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>7 days</option>
            <option value={30}>30 days</option>
            <option value={90}>90 days</option>
            <option value={180}>180 days</option>
          </select>
        </label>
      </div>
      {found.isPending ? (
        <Loading />
      ) : found.isError ? (
        <ErrorAlert error={found.error} />
      ) : found.data.signals.length === 0 ? (
        <Empty>
          Nothing unusual between {formatDate(found.data.start)} and {formatDate(found.data.end)}.
        </Empty>
      ) : (
        found.data.signals.map((s) => (
          <section key={s.key} className="card table-card" aria-label={s.title}>
            <div className="card-pad">
              <h2>
                {s.title} {s.serious && <span className="badge warn">Look at this</span>}
              </h2>
              <p className="muted small">{s.why}</p>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>What</th>
                    <th>Recorded by</th>
                    <th className="num">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {s.items.map((item, n) => (
                    <tr key={n}>
                      <td className="nowrap">{formatDate(item.day)}</td>
                      <td>{item.link ? <Link to={item.link}>{item.what}</Link> : item.what}</td>
                      <td>{item.who || <span className="muted">—</span>}</td>
                      <td className="num">
                        {item.amount_kobo === null || item.amount_kobo === undefined ? '' : formatKobo(item.amount_kobo)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))
      )}
    </>
  );
}

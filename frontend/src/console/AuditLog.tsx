import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { api, unwrap, type Schemas } from '../api/client';
import { formatDateTime, humanize } from '../lib/format';
import { Empty, ErrorAlert, Loading, PageHeader, Pager } from '../lib/ui';

type Entry = Schemas['AuditEntryOut'];
const PAGE = 50;

export function AuditLog({ platform = false }: { platform?: boolean }) {
  const [offset, setOffset] = useState(0);
  const entries = useQuery({
    queryKey: ['audit', platform, offset],
    queryFn: async () => {
      const query = { query: { limit: PAGE, offset } };
      return unwrap(platform ? await api.GET('/api/v1/platform/audit', { params: query }) : await api.GET('/api/v1/audit', { params: query }));
    },
  });

  return (
    <>
      <PageHeader title={platform ? 'Platform activity' : 'Activity log'} subtitle="Every sign-in and change to money or permissions, newest first. Entries can't be edited or deleted." />
      {entries.isPending ? (
        <Loading />
      ) : entries.isError ? (
        <ErrorAlert error={entries.error} />
      ) : entries.data.items.length === 0 ? (
        <Empty>Nothing recorded yet.</Empty>
      ) : (
        <div className="card table-card">
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Who</th>
                <th>What</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {entries.data.items.map((e) => (
                <tr key={e.id}>
                  <td className="nowrap">{formatDateTime(e.created_at)}</td>
                  <td>
                    {e.actor_label}
                    {e.ip_address && <div className="muted small">{e.ip_address}</div>}
                  </td>
                  <td>{humanize(e.action)}</td>
                  <td>
                    <Changes entry={e} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager total={entries.data.total} limit={PAGE} offset={offset} onChange={setOffset} />
        </div>
      )}
    </>
  );
}

function Changes({ entry }: { entry: Entry }) {
  const items = Object.entries(entry.changes);
  if (items.length === 0) return <span className="muted">—</span>;
  return (
    <ul className="changes">
      {items.map(([key, value]) => (
        <li key={key}>
          <span className="muted">{humanize(key)}:</span> {describe(value)}
        </li>
      ))}
    </ul>
  );
}

function describe(value: unknown): string {
  if (value && typeof value === 'object' && 'from' in value && 'to' in value) {
    const change = value as { from: unknown; to: unknown };
    return `${String(change.from ?? '—')} → ${String(change.to ?? '—')}`;
  }
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

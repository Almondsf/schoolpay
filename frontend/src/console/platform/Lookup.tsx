import { useMutation } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';

import { api, unwrap, type Schemas } from '../../api/client';
import { Empty, ErrorAlert, PageHeader } from '../../lib/ui';

const KIND_LABELS: Record<Schemas['SupportLookupHitOut']['kind'], string> = {
  school: 'School',
  staff: 'Staff member',
  payment: 'Payment',
  account_number: 'Student account number',
  receipt: 'Receipt',
};

/** Support: "a parent says they paid", "the bursar can't sign in". Every search is recorded. */
export function PlatformLookup() {
  const [q, setQ] = useState('');
  const search = useMutation({
    mutationFn: async (query: string) => unwrap(await api.GET('/api/v1/platform/lookup', { params: { query: { q: query } } })),
  });
  return (
    <>
      <PageHeader
        title="Support lookup"
        subtitle="Find a school, a staff member (exact email), a payment (gateway or bank reference), a student's account number or a receipt code. Students show as initials only, and every search is recorded."
      />
      <form
        className="filters"
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          if (q.trim().length >= 3) search.mutate(q.trim());
        }}
      >
        <input type="search" aria-label="Search" placeholder="e.g. TLR12345678 or bursar@school.ng" value={q} onChange={(e) => setQ(e.target.value)} />
        <button type="submit" disabled={q.trim().length < 3 || search.isPending}>
          Search
        </button>
      </form>
      <ErrorAlert error={search.error} />
      {search.data &&
        (search.data.length === 0 ? (
          <Empty>Nothing matches “{search.variables}”.</Empty>
        ) : (
          <div className="card">
            <ul className="plain">
              {search.data.map((hit, n) => (
                <li key={n} className="list-item">
                  <span className="badge">{KIND_LABELS[hit.kind]}</span> <strong>{hit.title}</strong>
                  <div className="small">
                    {hit.school_id ? <Link to={`/console/platform/schools/${hit.school_id}`}>{hit.school_name}</Link> : hit.school_name}
                    {' · '}
                    {hit.detail}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ))}
    </>
  );
}

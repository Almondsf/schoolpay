import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { api, unwrap } from '../../api/client';
import { useCurrentTerm } from '../hooks';

export interface PickedStudent {
  id: string;
  full_name: string;
  admission_number: string;
}

/** Search students by name or admission number and pick one. */
export function StudentPicker({ value, onChange }: { value: PickedStudent | null; onChange: (s: PickedStudent | null) => void }) {
  const current = useCurrentTerm();
  const [search, setSearch] = useState('');
  const results = useQuery({
    queryKey: ['student-search', search, current.data?.id],
    enabled: search.trim().length >= 2 && !!current.data,
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/students', { params: { query: { term_id: current.data!.id, search, limit: 8 } } })),
  });

  if (value) {
    return (
      <div className="row between picked">
        <span>
          <strong>{value.full_name}</strong> <span className="muted">({value.admission_number})</span>
        </span>
        <button type="button" className="link small" onClick={() => onChange(null)}>
          Change
        </button>
      </div>
    );
  }
  return (
    <div className="picker">
      <input
        aria-label="Find student"
        placeholder="Type a name or admission number"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        autoComplete="off"
      />
      {results.data && (
        <ul className="picker-results" role="listbox">
          {results.data.items.length === 0 && <li className="muted">No match</li>}
          {results.data.items.map((s) => (
            <li key={s.id}>
              <button type="button" role="option" aria-selected={false} className="link" onClick={() => onChange(s)}>
                {s.full_name} <span className="muted">({s.admission_number})</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

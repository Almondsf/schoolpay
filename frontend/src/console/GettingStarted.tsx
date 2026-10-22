import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { api, unwrap } from '../api/client';
import { useAuth } from '../auth/AuthContext';

/** A new school's getting-started steps, worked out from its data. Hidden once every step is done. */
export function GettingStarted() {
  const { can } = useAuth();
  const steps = useQuery({
    queryKey: ['onboarding'],
    queryFn: async () => unwrap(await api.GET('/api/v1/school/onboarding')),
    staleTime: 0,
  });
  if (!steps.data || steps.data.every((s) => s.done)) return null;
  const done = steps.data.filter((s) => s.done).length;
  return (
    <section className="card section" aria-label="Getting started">
      <div className="row between">
        <h2>Getting started</h2>
        <span className="muted small">
          {done} of {steps.data.length} done
        </span>
      </div>
      {can('school.manage') && (
        <p>
          <Link to="/console/get-started" className="button">
            Set up step by step
          </Link>{' '}
          <span className="muted small">Details, school year, classes, fees, pupils and bills, one at a time.</span>
        </p>
      )}
      <ol className="checklist">
        {steps.data.map((s) => (
          <li key={s.key} className={s.done ? 'done' : ''}>
            <span className="tick" aria-hidden="true">
              {s.done ? '✓' : ''}
            </span>
            <div>
              {s.done ? <span>{s.title}</span> : <Link to={s.link}>{s.title}</Link>}
              <div className="muted small">{s.hint}</div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

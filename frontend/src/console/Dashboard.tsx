import { useAuth } from '../auth/AuthContext';
import { PageHeader } from '../lib/ui';

/** Collections at a glance. Filled in by the fees, payments and reports chunks. */
export function Dashboard() {
  const { user } = useAuth();
  return (
    <>
      <PageHeader title={`Welcome, ${user?.full_name.split(' ')[0] ?? ''}`} subtitle={user?.school?.name} />
      <div className="card">
        <p>Your school's fee collection summary will appear here once fees and payments are set up.</p>
      </div>
    </>
  );
}

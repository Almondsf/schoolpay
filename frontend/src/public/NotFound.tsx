import { Link } from 'react-router-dom';

export function NotFound() {
  return (
    <main className="page page--narrow">
      <h1>Page not found</h1>
      <p>
        <Link to="/">Go to the start page</Link>
      </p>
    </main>
  );
}

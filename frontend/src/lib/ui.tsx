import { ArrowLeft } from 'lucide-react';
import { useEffect, useRef, type ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { ApiError } from '../api/client';
import { humanize } from './format';

const STATUS_TONE: Record<string, string> = {
  active: 'ok',
  suspended: 'danger',
  pending: 'warn',
  invited: 'warn',
  inactive: '',
};

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${STATUS_TONE[status] ?? ''}`}>{humanize(status)}</span>;
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

export function ErrorAlert({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : 'Something went wrong.';
  return (
    <div className="alert error" role="alert">
      {message}
    </div>
  );
}

export function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="alert info" role="status">
      {children}
    </div>
  );
}

export function Loading() {
  return (
    <div className="empty" aria-busy="true">
      Loading…
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="hint">{hint}</span>}
      {error && <span className="hint error-text">{error}</span>}
    </label>
  );
}

/** The message for one form field, when the API rejected that field. */
export function fieldError(error: unknown, field: string): string | undefined {
  return error instanceof ApiError && error.field === field ? error.message : undefined;
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    dialog.current?.querySelector<HTMLElement>('input, select, textarea, button')?.focus();
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} ref={dialog}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function Pager({
  total,
  limit,
  offset,
  onChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}) {
  if (total <= limit) return null;
  return (
    <div className="row pager">
      <button type="button" className="secondary" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        Previous
      </button>
      <span className="muted">
        {offset + 1}–{Math.min(offset + limit, total)} of {total}
      </span>
      <button type="button" className="secondary" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
        Next
      </button>
    </div>
  );
}

/** A row of tabs (a segmented control). Pass the tab list once; the active one is `value`. */
export function Tabs<K extends string>({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: readonly { key: K; label: ReactNode }[];
  value: K;
  onChange: (key: K) => void;
  label?: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button
          key={t.key}
          type="button"
          role="tab"
          aria-selected={value === t.key}
          className={value === t.key ? 'tab active' : 'tab'}
          onClick={() => onChange(t.key)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** A headline figure on a card: label, value, an optional icon chip and a note underneath. */
export function Stat({
  label,
  value,
  note,
  tone,
  icon,
  small,
  children,
}: {
  label: string;
  value: string;
  note?: ReactNode;
  tone?: 'ok' | 'warn' | 'danger' | 'info';
  icon?: ReactNode;
  small?: boolean;
  children?: ReactNode;
}) {
  return (
    <div className={`card stat ${tone ?? ''} ${small ? 'small-stat' : ''}`}>
      <div className="stat-head">
        <span className="stat-label">{label}</span>
        {icon && <span className={`stat-icon ${tone ?? ''}`}>{icon}</span>}
      </div>
      <div className="stat-value">{value}</div>
      {children}
      {note && <div className="stat-note">{note}</div>}
    </div>
  );
}

/**
 * A button that opens a short list of actions (e.g. "More", or ⋯ on a row). Clicking an action or
 * anywhere outside closes it. Built on <details>, so it works with the keyboard without extra code.
 */
export function Menu({ label, icon, children }: { label: string; icon?: ReactNode; children: ReactNode }) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) ref.current.open = false;
    };
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, []);
  return (
    <details
      className="menu"
      ref={ref}
      onClick={(event) => {
        if ((event.target as HTMLElement).closest('.menu-list button') && ref.current) ref.current.open = false;
      }}
    >
      <summary className={icon ? 'menu-button icon-only' : 'menu-button'} aria-label={icon ? label : undefined} title={label}>
        {icon ?? label}
      </summary>
      <div className="menu-list">{children}</div>
    </details>
  );
}

/** "← Students": back to the list a page was opened from. */
export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="back-link">
      <ArrowLeft size={16} aria-hidden="true" /> {children}
    </Link>
  );
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Banknote, CircleAlert, Landmark } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import { api, unwrap, type Schemas } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { formatDate, formatDateTime, formatKobo, lagosToday, parseNairaToKobo } from '../../lib/format';
import { Empty, ErrorAlert, Field, Loading, Modal, PageHeader, Stat, fieldError } from '../../lib/ui';

type Deposit = Schemas['CashDepositOut'];

function daysBefore(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

/**
 * The cash book: cash taken at the school, cash taken to the bank (checked by a second person
 * against the bank statement), what should be in the drawer, and each day's close with the count.
 */
export function CashBook() {
  const { can } = useAuth();
  const today = lagosToday();
  const [start, setStart] = useState(daysBefore(today, 29));
  const [end, setEnd] = useState(today);
  const [dialog, setDialog] = useState<'bank' | 'close' | null>(null);
  const book = useQuery({
    queryKey: ['cash-book', start, end],
    queryFn: async () => unwrap(await api.GET('/api/v1/cash-book', { params: { query: { start, end } } })),
  });
  const pending = useQuery({
    queryKey: ['cash-deposits', 'pending'],
    queryFn: async () => unwrap(await api.GET('/api/v1/cash-deposits', { params: { query: { status: 'pending' } } })),
  });

  return (
    <>
      <PageHeader
        title="Cash book"
        subtitle="Cash taken at the school, cash taken to the bank, and what should be in the drawer."
        actions={
          <>
            {can('payments.record') && (
              <button className="secondary" onClick={() => setDialog('bank')}>
                Record banking
              </button>
            )}
            {can('payments.approve') && <button onClick={() => setDialog('close')}>Close a day</button>}
          </>
        }
      />
      {book.isPending ? (
        <Loading />
      ) : book.isError ? (
        <ErrorAlert error={book.error} />
      ) : (
        <>
          <div className="stats">
            <Stat
              label="Should be in the drawer now"
              value={formatKobo(book.data.held_now_kobo)}
              icon={<Banknote size={18} />}
              note="Cash taken, less cash banked"
            />
            <Stat
              label="Banked, not yet checked"
              value={formatKobo(book.data.unchecked_banking_kobo)}
              icon={<Landmark size={18} />}
              tone={book.data.unchecked_banking_kobo > 0 ? 'warn' : undefined}
              note="Waiting for a second person to check the bank statement"
            />
            <Stat
              label="Last day closed"
              value={book.data.last_closed ? formatDate(book.data.last_closed) : 'Never'}
              icon={<CircleAlert size={18} />}
              tone={book.data.last_closed === today ? 'ok' : 'warn'}
            />
          </div>

          {pending.data && pending.data.length > 0 && <PendingBanking deposits={pending.data} canDecide={can('payments.approve')} />}

          {book.data.late.length > 0 && (
            <section className="card section" aria-label="Recorded after the day was closed">
              <h2>Recorded after the day was closed</h2>
              <p className="small muted">Check these with whoever recorded them: money for a closed day should be in that day's count.</p>
              <ul className="plain">
                {book.data.late.map((e, i) => (
                  <li key={i} className="list-item row between">
                    <span>
                      {e.kind === 'cash' ? 'Cash taken' : 'Banking'} for {formatDate(e.day)}, recorded {formatDateTime(e.recorded_at)} by{' '}
                      {e.recorded_by}
                    </span>
                    <strong>{formatKobo(e.amount_kobo)}</strong>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <div className="row wrap">
            <Field label="From">
              <input id="cash-from" type="date" value={start} max={end} onChange={(e) => setStart(e.target.value)} />
            </Field>
            <Field label="To">
              <input id="cash-to" type="date" value={end} min={start} max={today} onChange={(e) => setEnd(e.target.value)} />
            </Field>
          </div>

          {book.data.days.length === 0 ? (
            <Empty>No cash taken or banked in these dates.</Empty>
          ) : (
            <div className="card table-card">
              <table aria-label="Cash by day">
                <thead>
                  <tr>
                    <th>Day</th>
                    <th className="num">Cash taken</th>
                    <th className="num">Reversed</th>
                    <th className="num">Banked</th>
                    <th className="num">Should be held</th>
                    <th className="num">Counted</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="muted">
                    <td>Brought forward</td>
                    <td colSpan={3} />
                    <td className="num">{formatKobo(book.data.opening_kobo)}</td>
                    <td />
                  </tr>
                  {book.data.days.map((d) => (
                    <tr key={d.day}>
                      <td className="nowrap">{formatDate(d.day)}</td>
                      <td className="num">
                        {d.received_kobo ? formatKobo(d.received_kobo) : '—'}
                        {d.payments > 0 && <div className="muted small">{d.payments} payments</div>}
                      </td>
                      <td className="num">{d.reversed_kobo ? formatKobo(d.reversed_kobo) : '—'}</td>
                      <td className="num">{d.banked_kobo ? formatKobo(d.banked_kobo) : '—'}</td>
                      <td className="num">
                        <strong>{formatKobo(d.held_kobo)}</strong>
                      </td>
                      <td className="num">
                        {d.counted_kobo == null ? (
                          <span className="muted">Not closed</span>
                        ) : (
                          <>
                            {formatKobo(d.counted_kobo)}
                            {d.difference_kobo !== 0 && (
                              <div className={`small ${d.difference_kobo! < 0 ? 'danger-text' : ''}`}>
                                {d.difference_kobo! < 0 ? 'Short ' : 'Over '}
                                {formatKobo(Math.abs(d.difference_kobo!))}
                              </div>
                            )}
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {book.data.staff.length > 0 && (
            <section className="card section" aria-label="Cash by staff">
              <h2>By staff</h2>
              <table>
                <thead>
                  <tr>
                    <th>Staff</th>
                    <th className="num">Cash taken</th>
                    <th className="num">Banked</th>
                  </tr>
                </thead>
                <tbody>
                  {book.data.staff.map((s) => (
                    <tr key={s.user_id}>
                      <td>{s.name}</td>
                      <td className="num">
                        {formatKobo(s.received_kobo)} <span className="muted small">({s.payments})</span>
                      </td>
                      <td className="num">{formatKobo(s.banked_kobo)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </>
      )}
      {dialog === 'bank' && <RecordBanking onClose={() => setDialog(null)} />}
      {dialog === 'close' && <CloseDay onClose={() => setDialog(null)} />}
    </>
  );
}

function useRefresh() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ['cash-book'] });
    void queryClient.invalidateQueries({ queryKey: ['cash-deposits'] });
  };
}

function PendingBanking({ deposits, canDecide }: { deposits: Deposit[]; canDecide: boolean }) {
  const refresh = useRefresh();
  const [rejecting, setRejecting] = useState<Deposit | null>(null);
  const confirm = useMutation({
    mutationFn: async (id: string) =>
      unwrap(await api.POST('/api/v1/cash-deposits/{deposit_id}/confirm', { params: { path: { deposit_id: id } } })),
    onSuccess: refresh,
  });
  return (
    <section className="card section" aria-label="Banking to check">
      <h2>Banking to check against the bank statement</h2>
      <ErrorAlert error={confirm.error} />
      <ul className="plain">
        {deposits.map((d) => (
          <li key={d.id} className="list-item row between wrap">
            <span>
              <strong>{formatKobo(d.amount_kobo)}</strong> banked {formatDate(d.banked_on)}
              {d.bank_reference && <span className="muted"> · teller {d.bank_reference}</span>}
              {d.note && <div className="muted small">{d.note}</div>}
            </span>
            {canDecide && (
              <span className="row">
                <button className="small" onClick={() => confirm.mutate(d.id)} disabled={confirm.isPending}>
                  It's on the statement
                </button>
                <button className="small secondary" onClick={() => setRejecting(d)}>
                  Not on the statement
                </button>
              </span>
            )}
          </li>
        ))}
      </ul>
      {!canDecide && <p className="hint">Someone with approval rights checks these against the bank statement.</p>}
      {rejecting && <RejectBanking deposit={rejecting} onClose={() => setRejecting(null)} />}
    </section>
  );
}

function RejectBanking({ deposit, onClose }: { deposit: Deposit; onClose: () => void }) {
  const refresh = useRefresh();
  const [reason, setReason] = useState('');
  const reject = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/cash-deposits/{deposit_id}/reject', { params: { path: { deposit_id: deposit.id } }, body: { reason } }),
      ),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  return (
    <Modal title="Not on the bank statement" onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          reject.mutate();
        }}
      >
        <p>
          {formatKobo(deposit.amount_kobo)} banked {formatDate(deposit.banked_on)} will count as still in the drawer. Say what you found.
        </p>
        <ErrorAlert error={reject.error} />
        <Field label="What's wrong">
          <input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            required
            minLength={3}
            placeholder="e.g. Only ₦40,000 reached the bank"
          />
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="danger" disabled={reject.isPending}>
            Mark as not banked
          </button>
        </div>
      </form>
    </Modal>
  );
}

function RecordBanking({ onClose }: { onClose: () => void }) {
  const refresh = useRefresh();
  const [amount, setAmount] = useState('');
  const [bankedOn, setBankedOn] = useState(lagosToday());
  const [reference, setReference] = useState('');
  const [note, setNote] = useState('');
  const kobo = parseNairaToKobo(amount);
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST('/api/v1/cash-deposits', {
          body: { amount_kobo: kobo ?? 0, banked_on: bankedOn, bank_reference: reference, note },
        }),
      ),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  return (
    <Modal title="Record banking" onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <p>Cash taken from the drawer to the bank. Someone else then checks it against the bank statement.</p>
        <ErrorAlert error={fieldError(save.error, 'amount_kobo') || fieldError(save.error, 'banked_on') ? null : save.error} />
        <Field
          label="Amount banked (₦)"
          error={fieldError(save.error, 'amount_kobo') ?? (amount && kobo === null ? 'Not a valid amount' : undefined)}
        >
          <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" required />
        </Field>
        <Field label="Banked on" error={fieldError(save.error, 'banked_on')}>
          <input type="date" value={bankedOn} max={lagosToday()} onChange={(e) => setBankedOn(e.target.value)} required />
        </Field>
        <Field label="Teller or deposit slip number">
          <input value={reference} onChange={(e) => setReference(e.target.value)} maxLength={100} />
        </Field>
        <Field label="Note (optional)">
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} />
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={!kobo || save.isPending}>
            Record banking
          </button>
        </div>
      </form>
    </Modal>
  );
}

function CloseDay({ onClose }: { onClose: () => void }) {
  const refresh = useRefresh();
  const [day, setDay] = useState(lagosToday());
  const [counted, setCounted] = useState('');
  const [note, setNote] = useState('');
  const kobo = parseNairaToKobo(counted);
  const expected = useQuery({
    queryKey: ['cash-book', day, day],
    queryFn: async () => unwrap(await api.GET('/api/v1/cash-book', { params: { query: { start: day, end: day } } })),
  });
  const held = expected.data ? (expected.data.days.at(-1)?.held_kobo ?? expected.data.opening_kobo) : null;
  const close = useMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/cash-book/close', { body: { day, counted_kobo: kobo ?? 0, note } })),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  const difference = held !== null && kobo !== null ? kobo - held : null;
  return (
    <Modal title="Close a day" onClose={onClose}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          close.mutate();
        }}
      >
        <p>Count the cash in the drawer and enter it. The count is kept beside what the system expected and can't be changed later.</p>
        <ErrorAlert error={close.error} />
        <Field label="Day">
          <input type="date" value={day} max={lagosToday()} onChange={(e) => setDay(e.target.value)} required />
        </Field>
        {held !== null && (
          <p>
            Expected in the drawer: <strong>{formatKobo(held)}</strong>
          </p>
        )}
        <Field label="Cash counted (₦)" error={counted && kobo === null ? 'Not a valid amount' : undefined}>
          <input value={counted} onChange={(e) => setCounted(e.target.value)} inputMode="decimal" required />
        </Field>
        {difference !== null && difference !== 0 && (
          <p className={difference < 0 ? 'danger-text' : ''}>
            {difference < 0 ? 'Short by ' : 'Over by '}
            <strong>{formatKobo(Math.abs(difference))}</strong>. Add a note to explain it.
          </p>
        )}
        <Field label={difference ? 'Note (please explain the difference)' : 'Note (optional)'}>
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} required={!!difference} />
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={kobo === null || close.isPending}>
            Close the day
          </button>
        </div>
      </form>
    </Modal>
  );
}

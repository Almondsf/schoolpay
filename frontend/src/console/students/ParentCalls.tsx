import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { api, unwrap, type Schemas } from '../../api/client';
import { ErrorAlert, Field, Modal } from '../../lib/ui';

type Language = Schemas['Language'];

export const LANGUAGE_LABELS: Record<Language, string> = { en: 'English', pcm: 'Pidgin' };

/** Also phone a parent who doesn't read messages easily: overdue reminders and payment confirmations. */
export function ParentCallsDialog({
  guardian,
  onClose,
  onDone,
}: {
  guardian: Schemas['GuardianOut'];
  onClose: () => void;
  onDone: () => void;
}) {
  const [prefersCalls, setPrefersCalls] = useState(guardian.prefers_calls);
  const [language, setLanguage] = useState<Language>(guardian.language);
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.PUT('/api/v1/guardians/{guardian_id}/calls', {
          params: { path: { guardian_id: guardian.id } },
          body: { prefers_calls: prefersCalls, language },
        }),
      ),
    onSuccess: () => {
      onDone();
      onClose();
    },
  });
  return (
    <Modal title={`Phone calls to ${guardian.full_name}`} onClose={onClose}>
      <p className="small">
        For parents who find messages hard to read. They still get every text; a call also reads out what's owed and the child's account
        number slowly, twice, when a bill is overdue, and confirms each payment. Only if the school has calls switched on (Messages).
      </p>
      <ErrorAlert error={save.error} />
      <label className="checkbox">
        <input type="checkbox" checked={prefersCalls} onChange={(e) => setPrefersCalls(e.target.checked)} />
        Also phone this parent
      </label>
      <Field label="Language on the call">
        <select value={language} onChange={(e) => setLanguage(e.target.value as Language)} disabled={!prefersCalls}>
          {Object.entries(LANGUAGE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </Field>
      <div className="row end">
        <button className="secondary" onClick={onClose}>
          Cancel
        </button>
        <button onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>
    </Modal>
  );
}

import { useMutation } from '@tanstack/react-query';
import { Printer } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import { api } from '../../api/client';
import { errorMessage, filenameFrom, saveBlob } from '../../lib/download';
import { ErrorAlert, Field, Modal } from '../../lib/ui';
import { useClassLevels } from '../hooks';

/**
 * Printable pay cards for parents who don't use apps: the child's own account number in large
 * type and this term's fees with what's left to pay. One pupil, or a class at four to a page.
 */
export function PayCardButton({ studentId }: { studentId: string }) {
  const download = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.GET('/api/v1/students/{student_id}/pay-card', {
        params: { path: { student_id: studentId } },
        parseAs: 'blob',
      });
      if (!response.ok || !data) throw new Error(errorMessage(error) ?? 'Could not make the pay card.');
      saveBlob(data as Blob, filenameFrom(response, 'pay-card.pdf'));
    },
  });
  return (
    <>
      <button className="secondary small" onClick={() => download.mutate()} disabled={download.isPending}>
        <Printer size={15} /> Print pay card
      </button>
      {download.error && <ErrorAlert error={download.error} />}
    </>
  );
}

export function PrintPayCards({ classId, onClose }: { classId: string; onClose: () => void }) {
  const levels = useClassLevels();
  const [levelId, setLevelId] = useState(classId);
  const download = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.GET('/api/v1/pay-cards', { params: { query: { class_level_id: levelId } }, parseAs: 'blob' });
      if (!response.ok || !data) throw new Error(errorMessage(error) ?? 'Could not make the pay cards.');
      const name = levels.data?.find((l) => l.id === levelId)?.name ?? 'class';
      saveBlob(data as Blob, `pay-cards-${name.toLowerCase().replace(/\W+/g, '-')}.pdf`);
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    download.mutate();
  }

  return (
    <Modal title="Print pay cards" onClose={onClose}>
      <form onSubmit={submit}>
        <p className="muted">
          A card for each pupil in the class, four to an A4 page: their own account number in large type, and this term's fees with what's
          left to pay on each. Print them after the bills are ready and send them home; the amounts are as they stand today.
        </p>
        <ErrorAlert error={download.error} />
        <Field label="Class">
          <select value={levelId} onChange={(e) => setLevelId(e.target.value)} required>
            <option value="">Choose a class…</option>
            {(levels.data ?? [])
              .filter((l) => l.is_active)
              .map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
          </select>
        </Field>
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>
            Close
          </button>
          <button type="submit" disabled={!levelId || download.isPending}>
            <Printer size={16} /> {download.isPending ? 'Making cards…' : 'Download cards'}
          </button>
        </div>
        {download.isSuccess && <p className="success-text small">Downloaded. Open the file and print it.</p>}
      </form>
    </Modal>
  );
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { api, unwrap } from '../../api/client';
import { formatDate } from '../../lib/format';
import { ErrorAlert, Loading, Modal } from '../../lib/ui';

/**
 * A parent's family payment link: one link for all their children, no sign-in. It also
 * goes out in bills and reminders. Staff copy it to send by hand, or replace it if it
 * reached the wrong person (the old one stops working at once).
 */
export function FamilyLinkDialog({ guardianId, name, onClose }: { guardianId: string; name: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [copied, setCopied] = useState(false);
  const link = useQuery({
    queryKey: ['family-link', guardianId],
    staleTime: 0,
    queryFn: async () => unwrap(await api.GET('/api/v1/guardians/{guardian_id}/pay-link', { params: { path: { guardian_id: guardianId } } })),
  });
  const replace = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST('/api/v1/guardians/{guardian_id}/pay-link/replace', { params: { path: { guardian_id: guardianId } } })),
    onSuccess: (data) => queryClient.setQueryData(['family-link', guardianId], data),
  });
  return (
    <Modal title={`Payment link for ${name}`} onClose={onClose}>
      <p className="small">
        One link for all their children at the school: each child's account number, and card payment with the amount they choose per
        child. No sign-in. It's already in their bills and reminders.
      </p>
      <ErrorAlert error={link.error ?? replace.error} />
      {link.isPending ? (
        <Loading />
      ) : (
        link.data && (
          <>
            <input readOnly aria-label="Payment link" value={link.data.url} onFocus={(e) => e.target.select()} />
            <p className="hint">Works until {formatDate(link.data.expires_at)}; a new one goes out with messages after that.</p>
            <div className="row between wrap">
              <button
                className="secondary"
                disabled={replace.isPending}
                onClick={() =>
                  window.confirm('Replace this link? The old one stops working straight away.') && replace.mutate()
                }
              >
                Replace link
              </button>
              <button
                onClick={() =>
                  void navigator.clipboard?.writeText(link.data.url).then(() => {
                    setCopied(true);
                    setTimeout(() => setCopied(false), 2000);
                  })
                }
              >
                {copied ? 'Copied' : 'Copy link'}
              </button>
            </div>
          </>
        )
      )}
    </Modal>
  );
}

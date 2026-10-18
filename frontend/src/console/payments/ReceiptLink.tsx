import { useMutation } from '@tanstack/react-query';

import { api } from '../../api/client';

/** Download a receipt PDF (an authenticated request, so a plain link won't do). */
export function ReceiptLink({ receiptId, number }: { receiptId: string; number: number }) {
  const download = useMutation({
    mutationFn: async () => {
      const { data, response } = await api.GET('/api/v1/receipts/{receipt_id}/pdf', {
        params: { path: { receipt_id: receiptId } },
        parseAs: 'blob',
      });
      if (!response.ok || !data) throw new Error('Could not download the receipt.');
      const link = document.createElement('a');
      link.href = URL.createObjectURL(data as Blob);
      link.download = `receipt-${number}.pdf`;
      link.click();
      URL.revokeObjectURL(link.href);
    },
  });
  return (
    <button type="button" className="link small" onClick={() => download.mutate()} disabled={download.isPending} title="Download receipt">
      Receipt #{number}
    </button>
  );
}

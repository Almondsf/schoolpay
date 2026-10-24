import { useMutation } from '@tanstack/react-query';

import { api, unwrap, type Schemas } from '../../api/client';
import { errorMessage, filenameFrom, saveBlob } from '../../lib/download';

type ExportIn = Schemas['ExportIn'];

const CHECK_EVERY_MS = 1500;
const GIVE_UP_AFTER_MS = 10 * 60 * 1000;

const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

/**
 * Big files (the journal, the end-of-session pack) are built in the background so they never time out:
 * ask for the file, check back until it's ready, then download it.
 */
export function useBackgroundExport() {
  return useMutation({
    mutationFn: async (body: ExportIn) => {
      let status = unwrap(await api.POST('/api/v1/accounting/exports', { body }));
      const deadline = Date.now() + GIVE_UP_AFTER_MS;
      while (status.status === 'pending') {
        if (Date.now() > deadline) throw new Error('It’s still being prepared. Try again in a few minutes.');
        await wait(CHECK_EVERY_MS);
        status = unwrap(await api.GET('/api/v1/accounting/exports/{export_id}', { params: { path: { export_id: status.id } } }));
      }
      if (status.status === 'failed') throw new Error(status.error || 'Couldn’t prepare the file. Please try again.');
      const { data, error, response } = await api.GET('/api/v1/accounting/exports/{export_id}/download', {
        params: { path: { export_id: status.id } },
        parseAs: 'blob',
      });
      if (!response.ok || !data) throw new Error(errorMessage(error) ?? 'Could not download the file.');
      saveBlob(data as Blob, filenameFrom(response, status.filename));
    },
  });
}

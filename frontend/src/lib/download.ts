/** Save a Blob the API returned (downloads need the access token, so a plain link won't do). */
export function saveBlob(data: Blob, filename: string): void {
  const link = document.createElement('a');
  link.href = URL.createObjectURL(data);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

/** The server's chosen filename (Content-Disposition), or a fallback. */
export function filenameFrom(response: Response, fallback: string): string {
  const match = /filename="([^"]+)"/.exec(response.headers.get('Content-Disposition') ?? '');
  return match?.[1] ?? fallback;
}

/** The API's error message from a failed download (the body is JSON even though we asked for a file). */
export function errorMessage(error: unknown): string | null {
  const message = (error as { error?: { message?: unknown } } | undefined)?.error?.message;
  return typeof message === 'string' ? message : null;
}

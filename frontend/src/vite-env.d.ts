/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API origin when the app is served from a different host; unset = same origin */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

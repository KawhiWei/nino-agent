/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_NINO_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

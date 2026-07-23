export default {
  loginExpired: 'Session expired, please log in again',
  loginExpiredShort: 'Session expired',
  networkError: 'Network error',
  thinking: '💭 Thinking',

  // Backend error CODE -> localized message (generic, all surfaces: chat, settings, …).
  // Raw exception text that is not a known code is shown as-is. Reuses the same
  // code vocabulary as documents.docErrorCodes but with context-neutral wording.
  backendErrorCodes: {
    EMBED_MODEL_NOT_INSTALLED: 'Embedding model not installed — vectorization and semantic retrieval are unavailable. Install a model in "Settings → Embedding Model".',
  },
}

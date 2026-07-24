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
    LLM_BUDGET_EXCEEDED: 'The LLM call budget for this conversation was exceeded and the request was aborted to avoid a long hang. The upstream model may be responding too slowly — retry later or adjust the model configuration.',
  },
}

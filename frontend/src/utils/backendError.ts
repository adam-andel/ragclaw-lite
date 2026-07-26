import { i18n } from '@/i18n'

const PREFIX = 'errors.backendErrorCodes.'

// Resolve a backend error CODE (e.g. "EMBED_MODEL_NOT_INSTALLED", optionally
// "CODE: detail") to a localized message. Genuine exception text that is not a
// known code is returned untouched so raw errors still surface verbatim.
// Mirrors the doc-specific documents.docErrorCodes mechanism but is generic
// across all surfaces (chat, settings, …).
export function backendErrorMessage(raw?: string | null): string {
  if (!raw) return ''
  const code = raw.split(':', 1)[0]
  const detail = raw.slice(code.length + 1).trim()
  const key = PREFIX + code
  if (i18n.global.te(key)) {
    return i18n.global.t(key, detail ? { detail } : undefined)
  }
  return raw
}

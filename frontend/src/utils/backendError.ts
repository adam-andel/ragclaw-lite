// Copyright 2026 徐松夏（Xu Songxia）
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
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

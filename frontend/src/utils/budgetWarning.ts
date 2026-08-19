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
// Shared formatter for the backend's structured, non-blocking budget warnings.
//
// Per project convention the backend never ships user-facing copy: it emits a
// bare code plus interpolation params, and the frontend localizes it. The same
// shape is returned by four different save endpoints (Settings config, KB
// update, profile update, conversation pin), so the mapping lives here instead
// of being copy-pasted into every view.

export interface BudgetWarning {
  code: string
  params?: Record<string, unknown>
}

// Matches vue-i18n's `t` closely enough for our call sites without dragging the
// full i18n types in. Each view passes its own `t` from useI18n().
type Translate = (key: string, named?: Record<string, unknown>) => string

// Localize one warning. Unknown codes fall back to the raw code so a new
// backend warning is never silently swallowed.
export function budgetWarningText(w: BudgetWarning, t: Translate): string {
  const params: Record<string, unknown> = { ...(w.params || {}) }
  // `field` arrives as a bare identifier (system_prompt / kb_prompt /
  // user_memory / pinned_instruction); swap in its localized label before
  // interpolating so the message reads naturally.
  if (typeof params.field === 'string') {
    const fieldKey = `settings.budgetWarningFields.${params.field}`
    const label = t(fieldKey, {})
    params.field = label === fieldKey ? params.field : label
  }
  const key = `settings.budgetWarningCodes.${w.code}`
  const msg = t(key, params)
  return msg === key ? w.code : msg
}

export function budgetWarningTexts(
  list: BudgetWarning[] | undefined | null,
  t: Translate,
): string[] {
  return (list || []).map((w) => budgetWarningText(w, t))
}

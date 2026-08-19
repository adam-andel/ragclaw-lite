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
/**
 * Parse a backend timestamp into a Date object.
 *
 * The backend stores timestamps as naive UTC (no timezone suffix). When such a
 * string is passed to `new Date()` it is interpreted as the browser's LOCAL
 * time, shifting the value by the local UTC offset (e.g. 08:30 UTC shown as
 * 08:30 instead of 16:30 in UTC+8). This helper normalizes naive strings to UTC
 * so the resulting Date represents the correct instant, which locale-aware
 * formatters then render in the user's wall-clock time.
 *
 * Strings that already carry a timezone (trailing `Z`/`z` or an offset like
 * `+08:00`) are passed through unchanged.
 */
export function parseUtcTs(value?: string | null): Date | null {
  if (!value) return null
  const normalized = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(value) ? value : value + 'Z'
  const d = new Date(normalized)
  return isNaN(d.getTime()) ? null : d
}

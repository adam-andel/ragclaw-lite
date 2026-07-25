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

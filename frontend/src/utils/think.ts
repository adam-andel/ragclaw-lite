/** HTML-escape raw text so it renders safely inside innerHTML. */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

/**
 * Convert raw streaming text into HTML suitable for innerHTML.
 * - Text outside `<think>…</think>` is escaped.
 * - Complete `</think>` blocks become collapsed `<details>`.
 * - An open `<think>` (no closing tag yet) stays open so the user can
 *   watch streaming reasoning live.
 *
 * Caller is expected to append the blinking cursor `<span>` separately.
 */
export function renderStreamingHtml(raw: string): string {
  let html = ''
  let remaining = raw

  while (remaining.length > 0) {
    const startIdx = remaining.indexOf('<think>')
    if (startIdx === -1) {
      html += escapeHtml(remaining)
      break
    }

    // Text before the think block
    html += escapeHtml(remaining.slice(0, startIdx))
    remaining = remaining.slice(startIdx + '<think>'.length)

    const endIdx = remaining.indexOf('</think>')
    if (endIdx === -1) {
      // Still streaming — keep the block open
      html +=
        `<details class="think-block" open>` +
        `<summary>💭 思考过程</summary>` +
        `<div class="think-content">${escapeHtml(remaining)}</div>` +
        `</details>`
      break
    }

    // Complete block — collapsed by default
    html +=
      `<details class="think-block">` +
      `<summary>💭 思考过程</summary>` +
      `<div class="think-content">${escapeHtml(remaining.slice(0, endIdx))}</div>` +
      `</details>`
    remaining = remaining.slice(endIdx + '</think>'.length)
  }

  return html
}

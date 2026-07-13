import MarkdownIt from 'markdown-it'
import { i18n } from '@/i18n'

/** Shared markdown-it instance — mirrors the config used by ChatMessage.vue. */
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

/** HTML-escape raw text so it renders safely inside innerHTML. */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

/**
 * Convert raw streaming text into HTML suitable for innerHTML.
 * - Text outside `<think>…</think>` is rendered as Markdown so headings,
 *   lists, code blocks, etc. appear live as tokens arrive.
 * - Complete `</think>` blocks become collapsed `<details>`.
 * - An open `<think>` (no closing tag yet) stays open so the user can
 *   watch streaming reasoning live.
 *
 * Caller is expected to append the blinking cursor `<span>` separately.
 */
export function renderStreamingHtml(raw: string): string {
  // Normalize [File] markers the same way the final render does
  const text = raw.replace(/\[File\]/g, '📄')
  let html = ''
  let remaining = text

  while (remaining.length > 0) {
    const startIdx = remaining.indexOf('<think>')
    if (startIdx === -1) {
      html += md.render(remaining)
      break
    }

    // Text before the think block — render as Markdown
    html += md.render(remaining.slice(0, startIdx))
    remaining = remaining.slice(startIdx + '<think>'.length)

    const endIdx = remaining.indexOf('</think>')
    if (endIdx === -1) {
      // Still streaming — keep the block open
      html +=
        `<details class="think-block" open>` +
        `<summary>${i18n.global.t('errors.thinking')}</summary>` +
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

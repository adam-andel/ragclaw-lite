<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import MarkdownIt from 'markdown-it'
import { NTag, NButton, NIcon, NSpin } from 'naive-ui'
import { Copy, Refresh } from '@vicons/ionicons5'
import { currentLocale } from '@/i18n/useLocale'
import AppModal from '@/components/common/AppModal.vue'
import type { ChatMessage } from '@/types'
import { escapeHtml } from '@/utils/think'
import { getDocumentChunk, downloadDocument } from '@/api/documents'

import { useAuthStore } from '@/stores/auth'

const props = defineProps<{
  message: ChatMessage
  isStreaming?: boolean
  queuePosition?: number | null
  stageHint?: string | null
  searchKeyword?: string
  activeMatch?: boolean
}>()

const emit = defineEmits<{
  regenerate: [assistantMsgId: string]
}>()

const auth = useAuthStore()

const { t } = useI18n()

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

function formatTime(iso: string) {
  // Backend stores UTC naive datetimes; treat as UTC and convert to local time.
  const normalized = /[A-Z]|\+[0-9]{2}:[0-9]{2}$|-[0-9]{2}:[0-9]{2}$/.test(iso) ? iso : iso + 'Z'
  const d = new Date(normalized)
  if (isNaN(d.getTime())) return '-'
  return new Intl.DateTimeFormat(currentLocale.value, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(d)
}

const renderedContent = computed(() => {
  const raw = props.message.content || ''
  let html = ''
  let remaining = raw

  while (remaining.length > 0) {
    const startIdx = remaining.indexOf('<think>')
    if (startIdx === -1) {
      html += md.render(remaining)
      break
    }

    // Render text before think as normal markdown
    html += md.render(remaining.slice(0, startIdx))
    remaining = remaining.slice(startIdx + '<think>'.length)

    const endIdx = remaining.indexOf('</think>')
    if (endIdx === -1) {
      // Unclosed — render rest as markdown (shouldn't happen after stream finishes)
      html += md.render(remaining)
      break
    }

    // Build collapsible think block
    html +=
      `<details class="think-block">` +
      `<summary>${t('chat.thinkingProcess')}</summary>` +
      `<div class="think-content">${escapeHtml(remaining.slice(0, endIdx))}</div>` +
      `</details>`
    remaining = remaining.slice(endIdx + '</think>'.length)
  }

  // Filter out empty <pre><code></code></pre> and <hr> elements
  html = html.replace(/<pre><code><\/code><\/pre>\s*/g, '')
  html = html.replace(/<hr\s*\/?>\s*/g, '')

  return html
})

// Safely highlight keywords in already-rendered HTML: only walk text nodes to avoid breaking the tag structure.
// When active=true, add .active to the hit marker (current navigation item); otherwise it is a normal hit.
function highlightHtml(html: string, keyword: string, active = false): string {
  const kw = keyword.trim().toLowerCase()
  if (!kw) return html
  const tpl = document.createElement('template')
  tpl.innerHTML = html
  const walker = document.createTreeWalker(tpl.content, NodeFilter.SHOW_TEXT)
  const textNodes: Text[] = []
  let node: Node | null
  while ((node = walker.nextNode())) {
    const val = node.nodeValue || ''
    if (val.toLowerCase().includes(kw)) textNodes.push(node as Text)
  }
  for (const textNode of textNodes) {
    const text = textNode.nodeValue || ''
    const lower = text.toLowerCase()
    const frag = document.createDocumentFragment()
    let last = 0
    let idx: number
    while ((idx = lower.indexOf(kw, last)) !== -1) {
      if (idx > last) frag.appendChild(document.createTextNode(text.slice(last, idx)))
      const mark = document.createElement('mark')
      mark.className = active ? 'search-hit active' : 'search-hit'
      mark.textContent = text.slice(idx, idx + kw.length)
      frag.appendChild(mark)
      last = idx + kw.length
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)))
    textNode.parentNode?.replaceChild(frag, textNode)
  }
  return tpl.innerHTML
}

const displayHtml = computed(() => {
  if (!props.searchKeyword) return renderedContent.value
  try {
    return highlightHtml(renderedContent.value, props.searchKeyword, !!props.activeMatch)
  } catch {
    return renderedContent.value
  }
})

const steps = computed(() => props.message.agentSteps || [])

const copied = ref(false)
let copyTimer: number | null = null

async function copyText(content: string) {
  try {
    await navigator.clipboard.writeText(content)
  } catch {
    // fallback for older browsers / non-HTTPS
    const ta = document.createElement('textarea')
    ta.value = content
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    ta.style.left = '-9999px'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
  copied.value = true
  if (copyTimer) clearTimeout(copyTimer)
  copyTimer = window.setTimeout(() => { copied.value = false }, 1500)
}

function regenerate(msg: ChatMessage) {
  emit('regenerate', msg.id)
}

// --- Citation modal state ---
const showCitationModal = ref(false)
const citationFullContent = ref<Record<string, string>>({})
const loadingCitationContent = ref(false)

async function openAllCitations() {
  showCitationModal.value = true
  citationFullContent.value = {}
  loadingCitationContent.value = true
  try {
    await Promise.all(
      props.message.citations.map(async (c) => {
        const key = `${c.doc_id}-${c.chunk_index}`
        if (c.chunk_index == null) {
          citationFullContent.value[key] = t('chat.citationMissingChunk')
          return
        }
        try {
          const res = await getDocumentChunk(c.doc_id, c.chunk_index)
          if (res.data?.content) {
            citationFullContent.value[key] = res.data.content
          }
        } catch {
          // noop: leave empty to show fallback below
        }
      })
    )
  } finally {
    loadingCitationContent.value = false
  }
}

function getCitationContent(c: ChatMessage['citations'][number]) {
  const key = `${c.doc_id}-${c.chunk_index}`
  if (citationFullContent.value[key] != null) {
    return citationFullContent.value[key]
  }
  return t('chat.citationLoadFailed')
}

async function handleDownload(docId: string, filename: string) {
  try {
    const res = await downloadDocument(docId)
    const blob = new Blob([res.data])
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
  } catch (e: any) {
    // noop: axios interceptor already shows error
  }
}

// --- Streaming placeholder ---
const streamEl = ref<HTMLSpanElement>()
const hasStreamedContent = ref(false)
let observer: MutationObserver | null = null

function setupObserver() {
  hasStreamedContent.value = false
  observer?.disconnect()
  observer = null
  if (props.isStreaming && streamEl.value) {
    observer = new MutationObserver(() => {
      if (streamEl.value && streamEl.value.innerHTML.length > 0) {
        hasStreamedContent.value = true
        observer?.disconnect()
        observer = null
      }
    })
    observer.observe(streamEl.value, { childList: true, subtree: true, characterData: true })
  }
}

watch(() => props.isStreaming, (val) => {
  if (val) {
    nextTick(() => setupObserver())
  } else {
    observer?.disconnect()
    observer = null
  }
}, { immediate: true })

onBeforeUnmount(() => {
  observer?.disconnect()
})
</script>

<template>
  <div :class="['message-wrapper', message.role, { 'active-search-hit': activeMatch }]" :id="'msg-' + message.id">
    <div class="message-avatar">
      {{ message.role === 'user' ? '👤' : '🤖' }}
    </div>
    <div class="message-col">
    <div class="message-body">
      <div class="message-meta">
        <span class="role-label">{{ message.role === 'user' ? t('chat.you') : 'ERAG' }}</span>
        <span class="time">{{ formatTime(message.created_at) }}</span>
      </div>

      <!-- Processing timeline -->
      <details v-if="steps.length" class="agent-steps" :open="isStreaming">
        <summary>{{ t('chat.processSteps', { count: steps.length }) }}</summary>
        <ul class="agent-step-list">
          <li v-for="(s, i) in steps" :key="i" :class="'step-' + s.stage">
            <span class="step-msg">{{ s.message }}</span>
          </li>
        </ul>
      </details>

      <!-- Stage 1: streaming — innerHTML written by chatview (includes think-block + cursor) -->
      <template v-if="isStreaming">
        <div class="message-content streaming">
          <span ref="streamEl" :id="'stream-' + message.id"></span>
          <span v-show="!hasStreamedContent" class="thinking-placeholder">
            <template v-if="queuePosition != null && queuePosition > 0">
              {{ t('chat.queued', { count: queuePosition }) }}
            </template>
            <template v-else>{{ stageHint || t('chat.thinking') }}</template>
          </span>
        </div>
      </template>

      <!-- Stage 2: done — render Markdown via v-html (with highlighting when keywords are hit) -->
      <template v-else>
        <div class="message-content" v-html="displayHtml"></div>
      </template>

      <div v-if="!isStreaming && auth.isAdmin && ((message as any)._ttft || (message as any).ttft_ms)" class="ttft-badge">
        ⏱ TTFT {{ (message as any)._ttft || (message as any).ttft_ms || 0 }}ms &nbsp;|&nbsp; 🔍 {{ t('chat.retrieval') }} {{ (message as any)._retrieval || (message as any).retrieval_ms || 0 }}ms &nbsp;|&nbsp; 🧠 LLM {{ (message as any)._llm || (message as any).llm_ms || 0 }}ms
      </div>

      <div v-if="message.citations.length > 0 && !isStreaming" class="citations">
        <div
          class="citations-title"
          @click="openAllCitations"
          role="button"
          tabindex="0"
          @keydown.enter.prevent="openAllCitations"
          @keydown.space.prevent="openAllCitations"
        >
          {{ t('chat.citationSource', { count: message.citations.length }) }}
        </div>
      </div>

      <!-- All references summary Modal -->
      <AppModal v-model:show="showCitationModal" :title="t('chat.citationDetail')" size="wide">
        <NSpin :show="loadingCitationContent">
          <div class="citation-modal-body">
            <div v-for="(c, i) in message.citations" :key="i" class="citation-item">
              <div class="citation-item-header">
                <NTag size="small" type="info" :bordered="false">{{ i + 1 }}</NTag>
                <span
                  class="citation-item-name citation-item-download"
                  :title="t('chat.downloadX', { name: c.doc_name })"
                  @click.stop="handleDownload(c.doc_id, c.doc_name)"
                  role="button"
                  tabindex="0"
                  @keydown.enter.prevent="handleDownload(c.doc_id, c.doc_name)"
                  @keydown.space.prevent="handleDownload(c.doc_id, c.doc_name)"
                >{{ c.doc_name }}</span>
                <span class="citation-item-score">{{ (c.score * 100).toFixed(0) }}%</span>
              </div>
              <div class="citation-item-meta">
                <span v-if="c.heading">📂 {{ c.heading }}</span>
                <span v-if="c.chunk_index != null">Chunk #{{ c.chunk_index }}</span>
                <span v-if="c.page != null && c.page > 0">{{ t('chat.pageX', { page: c.page }) }}</span>
              </div>
              <pre class="citation-item-snippet">{{ getCitationContent(c) }}</pre>
            </div>
          </div>
        </NSpin>
      </AppModal>

    </div>
    <div v-if="!isStreaming && message.role === 'assistant'" class="message-actions">
      <div class="copy-btn-wrapper">
        <NButton text size="tiny" @click="copyText(message.content)" class="msg-action-btn">
          <template #icon><NIcon><Copy /></NIcon></template>
          {{ t('common.copy') }}
        </NButton>
        <Transition name="copy-tip-fade">
          <span v-if="copied" class="copy-tip">{{ t('common.copied') }}</span>
        </Transition>
      </div>
      <NButton text size="tiny" @click="regenerate(message)" class="msg-action-btn">
        <template #icon><NIcon><Refresh /></NIcon></template>
        {{ t('chat.regenerate') }}
      </NButton>
    </div>
    <div v-if="!isStreaming && message.role === 'user'" class="message-actions">
      <div class="copy-btn-wrapper">
        <NButton text size="tiny" @click="copyText(message.content)" class="msg-action-btn">
          <template #icon><NIcon><Copy /></NIcon></template>
          {{ t('common.copy') }}
        </NButton>
        <Transition name="copy-tip-fade">
          <span v-if="copied" class="copy-tip">{{ t('common.copied') }}</span>
        </Transition>
      </div>
    </div>
    </div>
  </div>
</template>

<style>
/* think-block — shared between streaming (innerHTML) and completed (v-html) */
.think-block {
  margin: var(--space-2) 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  overflow: hidden;
  background: var(--color-primary-soft);
}
.think-block > summary {
  cursor: pointer;
  padding: 6px var(--space-3);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-muted);
  user-select: none;
  list-style: none;
  display: flex;
  align-items: center;
  gap: 4px;
}
.think-block > summary::-webkit-details-marker {
  display: none;
}
.think-block > summary::before {
  content: '▸';
  display: inline-block;
  transition: transform 0.15s ease;
  font-size: 0.8em;
  margin-right: 2px;
}
.think-block[open] > summary::before {
  transform: rotate(90deg);
}
.think-block > summary:hover {
  background: var(--color-primary-soft);
  color: var(--color-text);
}
.think-content {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  line-height: 1.6;
  color: var(--color-text-muted);
  white-space: pre-wrap;
  word-break: break-word;
  border-top: 1px solid var(--color-border);
}

/* Search-hit highlight: normal hit (yellow) / current navigation item (orange); visible in both light and dark modes */
mark.search-hit {
  background: rgba(255, 196, 0, 0.6);
  color: inherit;
  border-radius: 2px;
  padding: 0 1px;
}
mark.search-hit.active {
  background: #ff8c1a;
  color: #1a1a1a;
}
</style>

<style scoped>
.message-wrapper {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-3) 0;
  animation: fadeIn 0.3s ease;
}
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }

/* Message currently hit by navigation: emphasized with a primary-color outline */
.message-wrapper.active-search-hit .message-body {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
}

.message-col {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  max-width: 75%;
}
.message-wrapper.user { flex-direction: row-reverse; }
.message-wrapper.user .message-col { align-items: flex-end; }
.message-avatar {
  width: 36px; height: 36px;
  display: flex; align-items: center; justify-content: center;
  border-radius: var(--radius);
  background: var(--color-border);
  font-size: var(--text-lg);
  flex-shrink: 0;
}
.message-body {
  width: fit-content;
  max-width: 100%;
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  box-shadow: var(--shadow-sm);
}
.user .message-body {
  /* Use naive-ui's primary color (#3b82f6, same as the type="primary" buttons in DocumentManage)
     so the bubble matches those buttons in BOTH light and dark mode. Note: --color-primary is #60a5fa
     in dark mode, which would make the bubble lighter than the primary buttons, hence the fixed value. */
  background: #3b82f6;
  color: white;
  border-color: transparent;
}
.message-meta {
  display: flex; align-items: center; gap: var(--space-2);
  margin-bottom: 6px; font-size: var(--text-sm);
}
.role-label { font-weight: 600; }
.time { color: var(--color-text-muted); }
.user .time { color: rgba(255,255,255,0.7); }

/* Dim the user bubble in dark mode — var(--color-primary) reads too bright on the dark surface */
:global(html.dark) .user .message-body {
  filter: brightness(0.85);
}

.agent-steps {
  margin-bottom: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-primary-soft);
  font-size: var(--text-sm);
}
.agent-steps > summary {
  cursor: pointer;
  padding: 6px var(--space-3);
  font-weight: 500;
  color: var(--color-text-muted);
  user-select: none;
  list-style: none;
  display: flex; align-items: center; gap: 4px;
}
.agent-steps > summary::-webkit-details-marker { display: none; }
.agent-steps > summary::before {
  content: '▸'; display: inline-block; transition: transform 0.15s ease; font-size: 0.8em; margin-right: 2px;
}
.agent-steps[open] > summary::before { transform: rotate(90deg); }
.agent-step-list {
  margin: 0; padding: 0 var(--space-3) var(--space-2) calc(var(--space-3) + 14px);
  list-style: none;
}
.agent-step-list li {
  padding: 2px 0; color: var(--color-text-muted); line-height: 1.6; word-break: break-word;
}
.message-content { line-height: 1.65; word-break: break-word; }
.message-content.streaming { display: flex; align-items: baseline; gap: 2px; }
.thinking-placeholder { color: var(--color-text-muted); font-style: italic; }
.cursor-blink { animation: blink 1s infinite; }
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }

/* Markdown rendering styles — only apply to the v-html branch */
.message-content :deep(h1),
.message-content :deep(h2),
.message-content :deep(h3) { margin: var(--space-3) 0 6px; font-weight: 600; line-height: 1.4; }
.message-content :deep(h1) { font-size: 1.25em; }
.message-content :deep(h2) { font-size: 1.1em; }
.message-content :deep(h3) { font-size: 1em; }
.message-content :deep(p) { margin: 6px 0; }
.message-content :deep(ul),
.message-content :deep(ol) { padding-left: 1.5em; margin: 6px 0; }
.message-content :deep(li) { margin: 2px 0; }
.message-content :deep(blockquote) {
  border-left: 3px solid var(--color-primary);
  padding: var(--space-1) var(--space-3); margin: var(--space-2) 0;
  color: var(--color-text-muted); background: var(--color-primary-soft);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}
.message-content :deep(table) {
  border-collapse: collapse; width: 100%; margin: var(--space-2) 0; font-size: 0.9em;
}
.message-content :deep(th),
.message-content :deep(td) {
  border: 1px solid var(--color-border); padding: 6px 10px; text-align: left;
}
.message-content :deep(th) { background: var(--color-primary-soft); font-weight: 600; }
.message-content :deep(pre) {
  background: rgba(0,0,0,0.08); border-radius: var(--radius);
  padding: var(--space-3) var(--space-4); overflow-x: auto; font-size: 0.85em; margin: var(--space-2) 0;
}
.message-content :deep(code) {
  font-family: 'JetBrains Mono', monospace;
  background: rgba(0,0,0,0.06); padding: 1px 5px; border-radius: var(--radius-sm); font-size: 0.88em;
}
.message-content :deep(pre code) { background: none; padding: 0; font-size: 1em; }
.user .message-content :deep(code) { background: rgba(255,255,255,0.15); }
.user .message-content :deep(pre) { background: rgba(255,255,255,0.1); }
.user .message-content :deep(blockquote) { border-color: rgba(255,255,255,0.3); color: rgba(255,255,255,0.7); background: rgba(255,255,255,0.05); }
.user .message-content :deep(th) { background: rgba(255,255,255,0.1); }
.user .message-content :deep(th),
.user .message-content :deep(td) { border-color: rgba(255,255,255,0.2); }
.user .message-content :deep(a) { color: rgba(255,255,255,0.9); }

.ttft-badge {
  margin-top: 6px; font-size: var(--text-xs); color: var(--color-text-muted);
  font-family: 'JetBrains Mono', monospace;
}

.citations {
  margin-top: var(--space-3); padding-top: var(--space-2);
  border-top: 1px solid var(--color-border);
  font-size: var(--text-sm);
}
.citations-title {
  font-weight: 600; margin-bottom: 6px;
  display: flex; align-items: center; gap: var(--space-2);
  cursor: pointer;
  color: var(--color-text);
}
.citations-title:hover {
  color: var(--color-primary);
  text-decoration: underline;
}
.citations-title:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
  border-radius: 2px;
}
.citation-chip-btn {
  display: inline-flex; align-items: center; gap: 6px;
  margin: 2px 4px 2px 0;
  font-size: var(--text-sm);
}
.citation-doc { font-weight: 500; color: var(--color-text); }
.citation-score { font-size: var(--text-xs); color: var(--color-text-muted); font-family: 'JetBrains Mono', monospace; }

/* —— Citation Modal —— */
.citation-modal-body {
  display: flex; flex-direction: column; gap: var(--space-4);
  max-height: calc(85vh - 120px); overflow-y: auto;
  padding-right: 4px;
}
.citation-item {
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  padding: var(--space-3);
  background: var(--color-surface);
}
.citation-item-header {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 6px;
}
.citation-item-name { font-weight: 600; }
.citation-item-download { cursor: pointer; color: var(--color-text); }
.citation-item-download:hover { color: var(--color-primary); text-decoration: underline; }
.citation-item-download:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; border-radius: 2px; }
.citation-item-score { font-size: var(--text-xs); color: var(--color-text-muted); font-family: 'JetBrains Mono', monospace; }
.citation-item-meta {
  font-size: var(--text-xs); color: var(--color-text-muted);
  display: flex; gap: var(--space-3);
  margin-bottom: 8px;
}
.citation-item-snippet {
  background: var(--color-primary-soft);
  border-radius: var(--radius-sm);
  padding: var(--space-3);
  font-size: var(--text-sm); line-height: 1.7;
  white-space: pre-wrap; word-break: break-word;
  margin: 0;
}

.message-actions {
  display: flex;
  gap: var(--space-1);
  margin-top: 4px;
  padding: 0 4px;
}
.msg-action-btn {
  font-size: var(--text-xs) !important;
  color: var(--color-text-muted) !important;
}
.msg-action-btn:hover {
  color: var(--color-text) !important;
}

.copy-btn-wrapper {
  position: relative;
  display: inline-flex;
}
.copy-tip {
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  margin-top: 2px;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--color-text);
  color: var(--color-bg);
  font-size: 11px;
  white-space: nowrap;
  pointer-events: none;
  z-index: 10;
}
.copy-tip-fade-enter-active,
.copy-tip-fade-leave-active {
  transition: opacity 0.15s ease;
}
.copy-tip-fade-enter-from,
.copy-tip-fade-leave-to {
  opacity: 0;
}
</style>

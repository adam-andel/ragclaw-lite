<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount, nextTick } from 'vue'
import MarkdownIt from 'markdown-it'
import { NTag, NButton, NIcon, NModal, NSpin } from 'naive-ui'
import { Copy, Refresh } from '@vicons/ionicons5'
import type { ChatMessage } from '@/types'
import { escapeHtml } from '@/utils/think'
import { getDocumentChunk, downloadDocument } from '@/api/documents'

import { useAuthStore } from '@/stores/auth'

const props = defineProps<{
  message: ChatMessage
  isStreaming?: boolean
  queuePosition?: number | null
}>()

const emit = defineEmits<{
  regenerate: [assistantMsgId: string]
}>()

const auth = useAuthStore()

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

const renderedContent = computed(() => {
  const raw = (props.message.content || '').replace(/\[File\]/g, '📄')
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
      `<summary>💭 思考过程</summary>` +
      `<div class="think-content">${escapeHtml(remaining.slice(0, endIdx))}</div>` +
      `</details>`
    remaining = remaining.slice(endIdx + '</think>'.length)
  }

  // Filter out empty <pre><code></code></pre> and <hr> elements
  html = html.replace(/<pre><code><\/code><\/pre>\s*/g, '')
  html = html.replace(/<hr\s*\/?>\s*/g, '')

  return html
})

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
          citationFullContent.value[key] = '该历史引用缺少分块索引，无法加载完整内容'
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
  return '加载失败，无法获取完整内容'
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
  <div :class="['message-wrapper', message.role]" :id="'msg-' + message.id">
    <div class="message-avatar">
      {{ message.role === 'user' ? '👤' : '🤖' }}
    </div>
    <div class="message-col">
    <div class="message-body">
      <div class="message-meta">
        <span class="role-label">{{ message.role === 'user' ? '你' : 'ERAG' }}</span>
        <span class="time">{{ formatTime(message.created_at) }}</span>
      </div>

      <!-- 阶段 1：流式中 — innerHTML 由 chatview 写入（含 think-block + cursor） -->
      <template v-if="isStreaming">
        <div class="message-content streaming">
          <span ref="streamEl" :id="'stream-' + message.id"></span>
          <span v-show="!hasStreamedContent" class="thinking-placeholder">
            <template v-if="queuePosition != null && queuePosition > 0">
              排队中，前面还有 {{ queuePosition }} 人
            </template>
            <template v-else>思考中……</template>
          </span>
        </div>
      </template>

      <!-- 阶段 2：完成后 — v-html 渲染 Markdown -->
      <template v-else>
        <div class="message-content" v-html="renderedContent"></div>
      </template>

      <div v-if="!isStreaming && auth.isAdmin && ((message as any)._ttft || (message as any).ttft_ms)" class="ttft-badge">
        ⏱ TTFT {{ (message as any)._ttft || (message as any).ttft_ms || 0 }}ms &nbsp;|&nbsp; 🔍 检索 {{ (message as any)._retrieval || (message as any).retrieval_ms || 0 }}ms &nbsp;|&nbsp; 🧠 LLM {{ (message as any)._llm || (message as any).llm_ms || 0 }}ms
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
          引用来源 · {{ message.citations.length }}
        </div>
      </div>

      <!-- 全部引用摘要 Modal -->
      <NModal v-model:show="showCitationModal" preset="card" title="引用来源详情" style="max-width: 720px; max-height: 85vh;">
        <NSpin :show="loadingCitationContent">
          <div class="citation-modal-body">
            <div v-for="(c, i) in message.citations" :key="i" class="citation-item">
              <div class="citation-item-header">
                <NTag size="small" type="info" :bordered="false">{{ i + 1 }}</NTag>
                <span
                  class="citation-item-name citation-item-download"
                  :title="`下载 ${c.doc_name}`"
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
                <span v-if="c.page != null && c.page > 0">第{{ c.page }}页</span>
              </div>
              <pre class="citation-item-snippet">{{ getCitationContent(c) }}</pre>
            </div>
          </div>
        </NSpin>
      </NModal>

    </div>
    <div v-if="!isStreaming && message.role === 'assistant'" class="message-actions">
      <NButton text size="tiny" @click="copyText(message.content)" class="msg-action-btn">
        <template #icon><NIcon><Copy /></NIcon></template>
        复制
      </NButton>
      <NButton text size="tiny" @click="regenerate(message)" class="msg-action-btn">
        <template #icon><NIcon><Refresh /></NIcon></template>
        重新生成
      </NButton>
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
</style>

<style scoped>
.message-wrapper {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-3) 0;
  animation: fadeIn 0.3s ease;
}
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }

.message-col {
  display: flex;
  flex-direction: column;
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
  max-width: 75%;
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  box-shadow: var(--shadow-sm);
}
.user .message-body {
  background: var(--color-primary);
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
.message-content { line-height: 1.65; word-break: break-word; }
.message-content.streaming { display: flex; align-items: baseline; gap: 2px; }
.thinking-placeholder { color: var(--color-text-muted); font-style: italic; }
.cursor-blink { animation: blink 1s infinite; }
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }

/* Markdown 渲染样式 — 仅对 v-html 分支生效 */
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
</style>

<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import { NTag, NButton, NIcon } from 'naive-ui'
import { Copy, Refresh } from '@vicons/ionicons5'
import type { ChatMessage } from '@/types'

import { useAuthStore } from '@/stores/auth'

const props = defineProps<{
  message: ChatMessage
  isStreaming?: boolean
}>()

const emit = defineEmits<{
  regenerate: [assistantMsgId: string]
}>()

const auth = useAuthStore()

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

const renderedContent = computed(() => md.render(props.message.content || ''))

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
</script>

<template>
  <div :class="['message-wrapper', message.role]" :id="'msg-' + message.id">
    <div class="message-avatar">
      {{ message.role === 'user' ? '👤' : '🤖' }}
    </div>
    <div class="message-body">
      <div class="message-meta">
        <span class="role-label">{{ message.role === 'user' ? '你' : 'ERAG' }}</span>
        <span class="time">{{ formatTime(message.created_at) }}</span>
      </div>

      <!-- 阶段 1：流式中 — v-once + textContent 保逐字效果 -->
      <template v-if="isStreaming">
        <div class="message-content streaming">
          <span v-once :id="'stream-' + message.id"></span>
          <span class="cursor-blink">▌</span>
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
        <div class="citations-title">📎 引用来源</div>
        <div v-for="(c, i) in message.citations" :key="i" class="citation-item">
          <NTag size="small" type="info">#{{ i + 1 }}</NTag>
          <span class="citation-doc">{{ c.doc_name }}</span>
          <span v-if="c.heading" class="citation-heading">{{ c.heading }}</span>
          <NTag size="small" :bordered="false">相似度 {{ (c.score * 100).toFixed(0) }}%</NTag>
        </div>
      </div>

      <div v-if="!isStreaming && message.role === 'assistant'" class="message-actions">
        <NButton text size="tiny" @click="copyText(message.content)">
          <template #icon><NIcon><Copy /></NIcon></template>
          复制
        </NButton>
        <NButton text size="tiny" @click="regenerate(message)">
          <template #icon><NIcon><Refresh /></NIcon></template>
          重新生成
        </NButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
.message-wrapper {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-3) 0;
  animation: fadeIn 0.3s ease;
}
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }

.message-wrapper.user { flex-direction: row-reverse; }
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
.citations-title { font-weight: 600; margin-bottom: 6px; }
.citation-item {
  display: flex; align-items: center; gap: 6px;
  padding: var(--space-1) 0; color: var(--color-text-muted);
}
.citation-doc { font-weight: 500; color: var(--color-text); }
.citation-heading { color: var(--color-primary); }

.message-actions {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px solid var(--color-border);
  opacity: 0;
  transition: opacity 0.15s ease;
}
.message-body:hover .message-actions {
  opacity: 1;
}
</style>

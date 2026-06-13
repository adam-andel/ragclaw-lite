<script setup lang="ts">
import { computed } from 'vue'
import { NTag } from 'naive-ui'
import type { ChatMessage } from '@/types'

import { useAuthStore } from '@/stores/auth'

const props = defineProps<{
  message: ChatMessage
  isStreaming?: boolean
}>()

const auth = useAuthStore()

function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function simpleRender(text: string): string {
  return text
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/\n/g, '<br>')
}

const renderedContent = computed(() => simpleRender(props.message.content))
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
      <div class="message-content"><span v-once :id="'stream-' + message.id">{{ message.content }}</span><span v-if="isStreaming" class="cursor-blink">▌</span></div>
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
    </div>
  </div>
</template>

<style scoped>
.message-wrapper {
  display: flex;
  gap: 12px;
  padding: 12px 0;
  animation: fadeIn 0.3s ease;
}
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }

.message-wrapper.user { flex-direction: row-reverse; }
.message-avatar {
  width: 36px; height: 36px;
  display: flex; align-items: center; justify-content: center;
  border-radius: 8px;
  background: var(--color-border);
  font-size: 1.1rem;
  flex-shrink: 0;
}
.message-body {
  max-width: 75%;
  background: var(--color-surface);
  border-radius: 12px;
  padding: 12px 16px;
  border: 1px solid var(--color-border);
}
.user .message-body { background: var(--color-primary); color: white; border-color: transparent; }
.message-meta {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 6px; font-size: 0.8rem;
}
.role-label { font-weight: 600; }
.time { color: var(--color-text-muted); }
.user .time { color: rgba(255,255,255,0.7); }
.message-content { line-height: 1.65; word-break: break-word; }
.message-content :deep(pre) {
  background: rgba(0,0,0,0.08); border-radius: 6px;
  padding: 8px 12px; overflow-x: auto; font-size: 0.85em;
}
.message-content :deep(code) {
  font-family: 'JetBrains Mono', monospace;
  background: rgba(0,0,0,0.06); padding: 1px 4px; border-radius: 3px; font-size: 0.88em;
}
.message-content :deep(pre code) { background: none; padding: 0; }
.cursor-blink { animation: blink 1s infinite; }
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
.ttft-badge {
  margin-top: 6px; font-size: 0.72rem; color: var(--color-text-muted);
  font-family: 'JetBrains Mono', monospace;
}

.citations {
  margin-top: 12px; padding-top: 10px;
  border-top: 1px solid var(--color-border);
  font-size: 0.85rem;
}
.citations-title { font-weight: 600; margin-bottom: 6px; }
.citation-item {
  display: flex; align-items: center; gap: 6px;
  padding: 4px 0; color: var(--color-text-muted);
}
.citation-doc { font-weight: 500; color: var(--color-text); }
.citation-heading { color: var(--color-primary); }
</style>

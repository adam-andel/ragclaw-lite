<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { NCard, NTag, NSpin } from 'naive-ui'
import { getOverview } from '@/api/stats'
import type { SystemStats } from '@/types'

const stats = ref<SystemStats | null>(null)
const loading = ref(true)

onMounted(async () => {
  try {
    const res = await getOverview()
    stats.value = res.data
  } catch { /* noop */ }
  loading.value = false
})

function formatTokens(cost: number) {
  if (cost < 1) return `¥${(cost * 100).toFixed(1)}分`
  return `¥${cost.toFixed(2)}`
}
</script>

<template>
  <div class="dashboard-view">
    <div class="dashboard-header">
      <h2>📊 系统概览</h2>
    </div>

    <NSpin :show="loading">
      <div v-if="stats" class="dashboard-body">
        <!-- Stat Cards -->
        <div class="stat-cards">
          <NCard size="small" class="stat-card">
            <div class="stat-value">{{ stats.document_count }}</div>
            <div class="stat-label">📄 文档总数</div>
          </NCard>
          <NCard size="small" class="stat-card">
            <div class="stat-value">{{ stats.chunk_count.toLocaleString() }}</div>
            <div class="stat-label">🧩 分块总数</div>
          </NCard>
          <NCard size="small" class="stat-card">
            <div class="stat-value">{{ stats.conversation_count }}</div>
            <div class="stat-label">💬 对话次数</div>
          </NCard>
          <NCard size="small" class="stat-card">
            <div class="stat-value">{{ formatTokens(stats.today_token_cost) }}</div>
            <div class="stat-label">💰 今日 Token 消耗</div>
          </NCard>
          <NCard size="small" class="stat-card">
            <div class="stat-value">{{ (stats.cache_hit_rate * 100).toFixed(1) }}%</div>
            <div class="stat-label">⚡ 缓存命中率</div>
          </NCard>
        </div>

        <!-- Hot Questions -->
        <NCard title="🔥 热门问题 Top 5" size="small" class="section-card">
          <div v-if="stats.hot_questions.length === 0" class="empty-text">暂无数据</div>
          <div v-for="(hq, i) in stats.hot_questions" :key="i" class="hot-item">
            <span class="hot-index">{{ i + 1 }}.</span>
            <span class="hot-question">{{ hq.question }}</span>
            <NTag size="tiny">{{ hq.count }} 次</NTag>
          </div>
        </NCard>

        <!-- Recent Conversations -->
        <NCard title="📋 最近对话" size="small" class="section-card">
          <div v-if="stats.recent_conversations.length === 0" class="empty-text">暂无对话</div>
          <div v-for="conv in stats.recent_conversations" :key="conv.id" class="recent-item">
            <span>{{ conv.title }}</span>
            <span class="recent-time">{{ new Date(conv.updated_at).toLocaleString('zh-CN') }}</span>
          </div>
        </NCard>
      </div>
    </NSpin>
  </div>
</template>

<style scoped>
.dashboard-view { max-width: 1000px; margin: 0 auto; }
.dashboard-header h2 { font-size: 1.25rem; margin-bottom: 20px; }
.stat-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 12px; margin-bottom: 24px; }
.stat-card { text-align: center; }
.stat-value { font-size: 1.8rem; font-weight: 700; color: var(--color-primary); }
.stat-label { font-size: 0.82rem; color: var(--color-text-muted); margin-top: 4px; }
.section-card { margin-bottom: 16px; }
.hot-item { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--color-border); }
.hot-item:last-child { border-bottom: none; }
.hot-index { color: var(--color-text-muted); font-weight: 600; min-width: 20px; }
.hot-question { flex: 1; }
.recent-item { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid var(--color-border); font-size: 0.88rem; }
.recent-item:last-child { border-bottom: none; }
.recent-time { color: var(--color-text-muted); font-size: 0.8rem; }
.empty-text { color: var(--color-text-muted); text-align: center; padding: 16px; }
</style>

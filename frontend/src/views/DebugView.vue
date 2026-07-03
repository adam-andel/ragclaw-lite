<script setup lang="ts">
import { ref } from 'vue'
import { NInput, NButton, NIcon, NSlider, NSpace, NTag, NCard, NEmpty, NSpin } from 'naive-ui'
import { Search } from '@vicons/ionicons5'
import { search } from '@/api/retrieval'
import type { SearchResult } from '@/types'

const query = ref('')
const results = ref<SearchResult[]>([])
const searching = ref(false)
const vectorWeight = ref(0.5)
const bm25Weight = ref(0.5)
const topK = ref(10)
const threshold = ref(0.3)

async function doSearch() {
  if (!query.value.trim()) return
  searching.value = true
  try {
    const res = await search({
      query: query.value,
      vector_weight: vectorWeight.value,
      bm25_weight: bm25Weight.value,
      top_k: topK.value,
      threshold: threshold.value,
    })
    results.value = res.data
  } catch {
    results.value = []
  } finally {
    searching.value = false
  }
}

function getScoreColor(score: number): string {
  if (score >= 0.8) return 'success'
  if (score >= 0.6) return 'info'
  if (score >= 0.4) return 'warning'
  return 'error'
}
</script>

<template>
  <div class="debug-view">
    <div class="debug-header">
      <h2>🔍 检索调试</h2>
      <p class="subtitle">可视化检索全过程，对比向量检索与 BM25 得分，调整融合参数</p>
    </div>

    <!-- Search Bar -->
    <div class="search-bar">
      <NInput
        v-model:value="query"
        placeholder="输入查询，如：微服务间通信方式、GR-809 型号规格..."
        size="large"
        @keydown.enter="doSearch"
      />
      <NButton type="primary" size="large" @click="doSearch" :loading="searching">
        <template #icon><NIcon><Search /></NIcon></template>
        检索
      </NButton>
    </div>

    <!-- Params -->
    <NSpace class="params-bar" align="center">
      <span class="param-label">向量权重</span>
      <NSlider v-model:value="vectorWeight" :min="0" :max="1" :step="0.05" style="width:150px" />
      <NTag size="small">{{ (vectorWeight * 100).toFixed(0) }}%</NTag>

      <span class="param-label">BM25 权重</span>
      <NSlider v-model:value="bm25Weight" :min="0" :max="1" :step="0.05" style="width:150px" />
      <NTag size="small">{{ (bm25Weight * 100).toFixed(0) }}%</NTag>

      <span class="param-label">返回数量</span>
      <NSlider v-model:value="topK" :min="3" :max="30" :step="1" style="width:120px" />
      <NTag size="small">{{ topK }}</NTag>

      <span class="param-label">阈值</span>
      <NSlider v-model:value="threshold" :min="0" :max="1" :step="0.05" style="width:120px" />
      <NTag size="small">{{ threshold }}</NTag>
    </NSpace>

    <!-- Results -->
    <NSpin :show="searching">
      <NEmpty v-if="results.length === 0 && !searching" description="输入查询后点击检索" />
      <div v-else class="results-list">
        <div class="results-summary">
          共召回 {{ results.length }} 条结果
          <span class="text-xs text-gray-400 ml-2">
            (向量Top-{{ 20 }}+BM25Top-{{ 20 }}→RRF融合→Top-{{ results.length }})
          </span>
        </div>

        <NCard v-for="(r, i) in results" :key="r.chunk_id" size="small" class="result-card">
          <div class="result-header">
            <NTag type="primary" size="small">#{{ i + 1 }}</NTag>
            <NSpace size="small">
              <NTag :type="getScoreColor(r.fusion_score) as any" size="small">
                融合 {{ (r.fusion_score * 100).toFixed(1) }}%
              </NTag>
              <NTag :type="getScoreColor(r.vector_score) as any" size="small">
                向量 {{ (r.vector_score * 100).toFixed(1) }}%
              </NTag>
              <NTag :type="getScoreColor(r.bm25_score) as any" size="small">
                BM25 {{ (r.bm25_score * 100).toFixed(1) }}%
              </NTag>
            </NSpace>
          </div>
          <div class="result-source">
            📄 <strong>{{ r.doc_name }}</strong>
            <span v-if="r.heading">· {{ r.heading }}</span>
            <span v-if="r.page">· 第{{ r.page }}页</span>
          </div>
          <p class="result-content">{{ r.content.slice(0, 400) }}{{ r.content.length > 400 ? '...' : '' }}</p>

          <!-- Score bars -->
          <div class="score-bars">
            <div class="score-bar">
              <span class="bar-label">向量</span>
              <div class="bar-track">
                <div class="bar-fill vector" :style="{ width: (r.vector_score * 100) + '%' }" />
              </div>
            </div>
            <div class="score-bar">
              <span class="bar-label">BM25</span>
              <div class="bar-track">
                <div class="bar-fill bm25" :style="{ width: (r.bm25_score * 100) + '%' }" />
              </div>
            </div>
            <div class="score-bar">
              <span class="bar-label">融合</span>
              <div class="bar-track">
                <div class="bar-fill fusion" :style="{ width: (r.fusion_score * 100) + '%' }" />
              </div>
            </div>
          </div>
        </NCard>
      </div>
    </NSpin>
  </div>
</template>

<style scoped>
.debug-view { max-width: 1000px; margin: 0 auto; }
.debug-header { margin-bottom: 20px; }
.debug-header h2 { font-size: 1.25rem; }
.subtitle { color: var(--color-text-muted); font-size: 0.9rem; margin-top: 4px; }
.search-bar { display: flex; gap: 12px; margin-bottom: 16px; }
.search-bar :deep(.n-input) { flex: 1; }
.params-bar { margin-bottom: 24px; padding: 12px 16px; background: var(--color-surface); border-radius: var(--radius); border: 1px solid var(--color-border); flex-wrap: wrap; }
.param-label { font-size: 0.82rem; color: var(--color-text-muted); min-width: 60px; }
.results-summary { font-size: 0.88rem; color: var(--color-text-muted); margin-bottom: 12px; }
.result-card { margin-bottom: 12px; }
.result-header { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.result-source { font-size: 0.88rem; margin-bottom: 6px; }
.result-content { font-size: 0.85rem; color: var(--color-text); line-height: 1.6; }
.score-bars { display: flex; flex-direction: column; gap: 4px; margin-top: 10px; }
.score-bar { display: flex; align-items: center; gap: 8px; }
.bar-label { font-size: 0.7rem; width: 30px; text-align: right; color: var(--color-text-muted); }
.bar-track { flex: 1; height: 6px; background: var(--color-border); border-radius: 3px; overflow: hidden; }
.bar-fill { height: 100%; border-radius: 3px; transition: width 0.4s ease; }
.bar-fill.vector { background: #818cf8; }
.bar-fill.bm25 { background: #34d399; }
.bar-fill.fusion { background: var(--color-primary); }
</style>

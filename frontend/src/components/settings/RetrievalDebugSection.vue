<!--
  Copyright 2026 徐松夏（Xu Songxia）

  Licensed under the Apache License, Version 2.0 (the "License");
  you may not use this file except in compliance with the License.
  You may obtain a copy of the License at

      http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software
  distributed under the License is distributed on an "AS IS" BASIS,
  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  See the License for the specific language governing permissions and
  limitations under the License.
-->
<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { NInput, NButton, NIcon, NSlider, NSpace, NTag, NCard, NEmpty, NSpin } from 'naive-ui'
import { Search } from '@vicons/ionicons5'
import { search } from '@/api/retrieval'
import type { SearchResult } from '@/types'

const { t } = useI18n()

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
  <div class="retrieval-section">
    <!-- Search Bar -->
    <div class="search-bar">
      <NInput
        v-model:value="query"
        :placeholder="t('retrieval.placeholder')"
        size="large"
        @keydown.enter="doSearch"
      />
      <NButton type="primary" size="large" @click="doSearch" :loading="searching">
        <template #icon><NIcon><Search /></NIcon></template>
        {{ t('retrieval.search') }}
      </NButton>
    </div>

    <!-- Params -->
    <NSpace class="params-bar" align="center">
      <span class="param-label">{{ t('retrieval.vectorWeight') }}</span>
      <NSlider v-model:value="vectorWeight" :min="0" :max="1" :step="0.05" style="width:150px" />
      <NTag size="small">{{ (vectorWeight * 100).toFixed(0) }}%</NTag>

      <span class="param-label">{{ t('retrieval.bm25Weight') }}</span>
      <NSlider v-model:value="bm25Weight" :min="0" :max="1" :step="0.05" style="width:150px" />
      <NTag size="small">{{ (bm25Weight * 100).toFixed(0) }}%</NTag>

      <span class="param-label">{{ t('retrieval.topK') }}</span>
      <NSlider v-model:value="topK" :min="3" :max="30" :step="1" style="width:120px" />
      <NTag size="small">{{ topK }}</NTag>

      <span class="param-label">{{ t('retrieval.threshold') }}</span>
      <NSlider v-model:value="threshold" :min="0" :max="1" :step="0.05" style="width:120px" />
      <NTag size="small">{{ threshold }}</NTag>
    </NSpace>

    <!-- Results -->
    <NSpin :show="searching">
      <NEmpty v-if="results.length === 0 && !searching" :description="t('retrieval.empty')" />
      <div v-else class="results-list">
        <div class="results-summary">
          {{ t('retrieval.resultSummary', { n: results.length }) }}
          <span class="text-xs text-gray-400 ml-2">
            {{ t('retrieval.fusionHint', { v: 20, b: 20, r: results.length }) }}
          </span>
        </div>

        <NCard v-for="(r, i) in results" :key="r.chunk_id" size="small" class="result-card">
          <div class="result-header">
            <NTag type="primary" size="small">#{{ i + 1 }}</NTag>
            <NSpace size="small">
              <NTag :type="getScoreColor(r.fusion_score) as any" size="small">
                {{ t('retrieval.scoreFusion') }} {{ (r.fusion_score * 100).toFixed(1) }}%
              </NTag>
              <NTag :type="getScoreColor(r.vector_score) as any" size="small">
                {{ t('retrieval.scoreVector') }} {{ (r.vector_score * 100).toFixed(1) }}%
              </NTag>
              <NTag :type="getScoreColor(r.bm25_score) as any" size="small">
                {{ t('retrieval.scoreBm25') }} {{ (r.bm25_score * 100).toFixed(1) }}%
              </NTag>
            </NSpace>
          </div>
          <div class="result-source">
            📄 <strong>{{ r.doc_name }}</strong>
            <span v-if="r.heading">· {{ r.heading }}</span>
            <span v-if="r.page">· {{ t('retrieval.page', { n: r.page }) }}</span>
          </div>
          <p class="result-content">{{ r.content.slice(0, 400) }}{{ r.content.length > 400 ? '...' : '' }}</p>

          <!-- Score bars -->
          <div class="score-bars">
            <div class="score-bar">
              <span class="bar-label">{{ t('retrieval.barVector') }}</span>
              <div class="bar-track">
                <div class="bar-fill vector" :style="{ width: (r.vector_score * 100) + '%' }" />
              </div>
            </div>
            <div class="score-bar">
              <span class="bar-label">{{ t('retrieval.barBm25') }}</span>
              <div class="bar-track">
                <div class="bar-fill bm25" :style="{ width: (r.bm25_score * 100) + '%' }" />
              </div>
            </div>
            <div class="score-bar">
              <span class="bar-label">{{ t('retrieval.barFusion') }}</span>
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
.retrieval-section { width: 100%; }
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
.bar-fill.vector { background: #60a5fa; }
.bar-fill.bm25 { background: #34d399; }
.bar-fill.fusion { background: var(--color-primary); }
</style>

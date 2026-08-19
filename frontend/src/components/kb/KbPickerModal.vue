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
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { NInput, NSelect, NIcon, NEmpty, NButton } from 'naive-ui'
import { Search, Create } from '@vicons/ionicons5'
import AppModal from '@/components/common/AppModal.vue'
import AppPagination from '@/components/common/AppPagination.vue'
import KbPickCard from '@/components/kb/KbPickCard.vue'

const { t } = useI18n()

const props = withDefaults(defineProps<{
  show: boolean
  kbs: any[]
  selectedId?: string | null
  title?: string
  /** Whether to show the special "All / Unlinked" card (used in filter scenarios) */
  showAll?: boolean
  allLabel?: string
  allMeta?: string
  allActive?: boolean
  allCount?: number
  /** Whether to show the sort dropdown (used on the document management page, not on the chat page) */
  sortable?: boolean
  pageSize?: number
  searchPlaceholder?: string
  /** Whether to show a "None / No KB selected" card to allow clearing the selection (chat page) */
  showNone?: boolean
  noneLabel?: string
  noneActive?: boolean
}>(), {
  selectedId: null,
  title: '',
  showAll: false,
  allLabel: 'All',
  allMeta: '',
  allActive: false,
  allCount: 0,
  sortable: false,
  pageSize: 12,
  searchPlaceholder: 'Search knowledge base...',
  showNone: false,
  noneLabel: '',
  noneActive: false,
})

const emit = defineEmits<{
  (e: 'update:show', v: boolean): void
  (e: 'select', id: string | null): void
  (e: 'create'): void
}>()

// Resolve the title in setup scope: defineProps/withDefaults defaults cannot
// reference the locally-declared `t`, because the macro is hoisted out of setup.
const resolvedTitle = computed(() => props.title || t('kb.selectTitle'))

const search = ref('')
const sortBy = ref<'recent' | 'doc_count'>('recent')
const page = ref(1)

const sortOptions = computed(() => [
  { label: t('kb.sort.recentUpdate'), value: 'recent' },
  { label: t('kb.sort.docCount'), value: 'doc_count' },
])

const filtered = computed(() => {
  let list = props.kbs
  const q = search.value.trim().toLowerCase()
  if (q) {
    list = list.filter((kb: any) =>
      kb.name.toLowerCase().includes(q) ||
      (kb.description && kb.description.toLowerCase().includes(q))
    )
  }
  if (props.sortable) {
    const copy = [...list]
    if (sortBy.value === 'doc_count') {
      copy.sort((a: any, b: any) => b.doc_count - a.doc_count)
    } else {
      copy.sort((a: any, b: any) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
    }
    return copy
  }
  return list
})

const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / props.pageSize)))
const paged = computed(() => {
  const start = (page.value - 1) * props.pageSize
  return filtered.value.slice(start, start + props.pageSize)
})

watch([search, sortBy], () => { page.value = 1 })

function onCardClick(id: string | null) {
  emit('select', id)
}
function onAfterLeave() {
  search.value = ''
  sortBy.value = 'recent'
  page.value = 1
}
</script>

<template>
  <AppModal
    :show="show"
    :title="resolvedTitle"
    size="wide"
    @update:show="emit('update:show', $event)"
    @after-leave="onAfterLeave"
  >
    <div class="kb-picker-toolbar">
      <NInput v-model:value="search" :placeholder="searchPlaceholder" clearable>
        <template #prefix><NIcon size="15"><Search /></NIcon></template>
      </NInput>
      <NSelect v-if="sortable" v-model:value="sortBy" :options="sortOptions" style="width: 140px" />
    </div>

    <div class="kb-picker-grid">
      <KbPickCard
        v-if="showNone"
        variant="none"
        :active="noneActive"
        :name="noneLabel || t('kb.noneKb')"
        @select="onCardClick($event)"
      />

      <KbPickCard
        v-if="showAll && kbs.length > 0"
        variant="all"
        :active="allActive"
        :name="allLabel"
        :soft-chip="t('kb.totalCount', { count: allCount })"
        :meta="allMeta"
        @select="onCardClick($event)"
      />

      <KbPickCard
        v-for="kb in paged"
        :key="kb.id"
        variant="normal"
        :kb="kb"
        :active="kb.id === selectedId"
        :select-id="kb.id"
        :stats="[`${kb.doc_count} ${t('kb.docUnit')}`, `${kb.vector_count} ${t('kb.chunkUnit')}`]"
        @select="onCardClick($event)"
      />

    <NEmpty v-if="filtered.length === 0 && kbs.length > 0" :description="t('kb.noMatch')" style="padding:16px 0" />
    <AppPagination
      v-if="kbs.length > 0"
      :page="page"
      :page-size="pageSize"
      :item-count="filtered.length"
      @update:page="(p: number) => page = p"
    />
    </div>

    <div v-if="kbs.length === 0" class="kb-picker-empty">
      <NEmpty :description="t('kb.noKbsYet')" style="padding:16px 0" />
      <NButton type="primary" @click="emit('create')">
        <template #icon><NIcon><Create /></NIcon></template>
        {{ t('documents.newKb') }}
      </NButton>
    </div>
  </AppModal>
</template>

<style scoped>
.kb-picker-toolbar { display: flex; gap: 8px; margin-bottom: 12px; }
.kb-picker-empty { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 8px 0 4px; }
.kb-picker-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  padding-top: 2px; /* prevent hover border-top clipping from overflow:auto parent */
}

@media (max-width: 640px) {
  .kb-picker-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 420px) {
  .kb-picker-grid { grid-template-columns: 1fr; }
  .kb-picker-toolbar { flex-direction: column; }
  .kb-picker-toolbar :deep(.n-base-selection) { width: 100% !important; }
}
</style>

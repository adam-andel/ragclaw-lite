<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { NInput, NSelect, NIcon, NEmpty, NButton } from 'naive-ui'
import { Search, Create } from '@vicons/ionicons5'
import AppModal from '@/components/common/AppModal.vue'
import AppCard from '@/components/common/AppCard.vue'
import AppPagination from '@/components/common/AppPagination.vue'

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
  allLabel: '全部',
  allMeta: '',
  allActive: false,
  allCount: 0,
  sortable: false,
  pageSize: 12,
  searchPlaceholder: '搜索知识库名称...',
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
      <AppCard
        v-if="showNone"
        class="kb-picker-card"
        :active="noneActive"
        role="button"
        tabindex="0"
        @click="onCardClick(null)"
        @keydown.enter.prevent="onCardClick(null)"
        @keydown.space.prevent="onCardClick(null)"
      >
        <div class="kb-picker-inner">
          <div class="kb-picker-avatar kb-picker-avatar-all">🚫</div>
          <div class="kb-picker-body">
            <strong class="kb-picker-name">{{ noneLabel || t('kb.noneKb') }}</strong>
          </div>
        </div>
      </AppCard>

      <AppCard
        v-if="showAll && kbs.length > 0"
        class="kb-picker-card"
        :active="allActive"
        role="button"
        tabindex="0"
        @click="onCardClick(null)"
        @keydown.enter.prevent="onCardClick(null)"
        @keydown.space.prevent="onCardClick(null)"
      >
        <div class="kb-picker-inner">
          <div class="kb-picker-avatar kb-picker-avatar-all">🗂️</div>
          <div class="kb-picker-body">
            <strong class="kb-picker-name">{{ allLabel }}</strong>
            <div class="kb-picker-stats">
              <span class="kb-picker-chip kb-picker-chip-soft">{{ t('kb.totalCount', { count: allCount }) }}</span>
            </div>
            <span v-if="allMeta" class="kb-picker-meta">{{ allMeta }}</span>
          </div>
        </div>
      </AppCard>

      <AppCard
        v-for="kb in paged"
        :key="kb.id"
        class="kb-picker-card"
        :active="kb.id === selectedId"
        role="button"
        tabindex="0"
        @click="onCardClick(kb.id)"
        @keydown.enter.prevent="onCardClick(kb.id)"
        @keydown.space.prevent="onCardClick(kb.id)"
      >
        <div class="kb-picker-inner">
          <div class="kb-picker-avatar">📚</div>
          <div class="kb-picker-body">
            <strong class="kb-picker-name">{{ kb.name }}</strong>
            <span v-if="kb.description" class="kb-picker-desc">{{ kb.description }}</span>
            <div class="kb-picker-stats">
              <span class="kb-picker-chip">{{ kb.doc_count }} {{ t('kb.docUnit') }}</span>
              <span class="kb-picker-chip">{{ kb.vector_count }} {{ t('kb.chunkUnit') }}</span>
            </div>
          </div>
        </div>
      </AppCard>
    </div>

    <NEmpty v-if="filtered.length === 0 && kbs.length > 0" :description="t('kb.noMatch')" style="padding:16px 0" />
    <AppPagination
      v-if="kbs.length > 0"
      :page="page"
      :page-size="pageSize"
      :item-count="filtered.length"
      @update:page="(p: number) => page = p"
    />

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
.kb-picker-inner { display: flex; align-items: flex-start; gap: 10px; }
.kb-picker-avatar {
  flex-shrink: 0;
  width: 36px; height: 36px;
  border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  font-size: 18px;
  background: var(--color-primary-soft);
}
.kb-picker-avatar-all { background: var(--color-border); }
.kb-picker-body { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 4px; }
.kb-picker-name { font-size: 14px; font-weight: 600; color: var(--color-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-picker-desc { font-size: var(--text-xs); color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-picker-stats { display: flex; flex-wrap: wrap; gap: 6px; }
.kb-picker-chip {
  font-size: 0.7rem; line-height: 1.4;
  color: var(--color-text-muted);
  background: var(--color-surface-2, #f1f5f9);
  border: 1px solid var(--color-border);
  border-radius: 9999px;
  padding: 1px 8px;
}
.kb-picker-chip-soft { color: var(--color-primary); background: var(--color-primary-soft); border-color: transparent; }
.kb-picker-meta { font-size: 0.7rem; color: var(--color-text-muted); }

@media (max-width: 640px) {
  .kb-picker-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 420px) {
  .kb-picker-grid { grid-template-columns: 1fr; }
  .kb-picker-toolbar { flex-direction: column; }
  .kb-picker-toolbar :deep(.n-base-selection) { width: 100% !important; }
}
</style>

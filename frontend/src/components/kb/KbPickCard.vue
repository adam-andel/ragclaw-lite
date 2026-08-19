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
/**
 * Shared knowledge-base picker card used by both the chat new-conversation
 * panel (ChatView) and the KB picker modal (KbPickerModal). Consolidates the
 * previously copy-pasted `.kb-pick-card` / `.kb-picker-card` markup + styles.
 *
 * Pure presentational + controlled: parent owns selection state and receives
 * `select` with the kb id (or null for the none/all cards). Text content is
 * passed in pre-translated by the caller so the two i18n domains (chat.* /
 * kb.*) stay decoupled from this generic card.
 *
 * variant:
 *  - 'none': clear selection (🚫)
 *  - 'all' : the special "All / Unlinked" filter card (🗂️) — only used by the modal
 *  - 'normal': a real KB (📚), content derived from `kb`
 */
import { computed } from 'vue'
import AppCard from '@/components/common/AppCard.vue'
import type { KnowledgeBase } from '@/types'

const props = withDefaults(
  defineProps<{
    variant?: 'none' | 'all' | 'normal'
    kb?: KnowledgeBase
    active?: boolean
    /** Emitted id for `select`; none/all cards pass null, normal passes kb.id */
    selectId?: string | null
    name?: string
    description?: string
    /** Stats chips for the normal variant, already formatted as text by caller */
    stats?: string[]
    /** Soft chip + meta line for the all variant */
    softChip?: string
    meta?: string
    disabled?: boolean
  }>(),
  {
    variant: 'normal',
    kb: undefined,
    active: false,
    selectId: null,
    name: '',
    description: '',
    stats: () => [],
    softChip: '',
    meta: '',
    disabled: false,
  },
)

const emit = defineEmits<{
  (e: 'select', id: string | null): void
}>()

const avatarIcon = computed(() => {
  if (props.variant === 'none') return '🚫'
  if (props.variant === 'all') return '🗂️'
  return '📚'
})
const avatarClass = computed(() => (props.variant !== 'normal' ? 'kb-pick-avatar-all' : ''))

// For normal cards, fall back to kb fields when name/stats not passed explicitly.
const displayName = computed(() => props.name || props.kb?.name || '')
const displayDesc = computed(() => props.description || props.kb?.description || '')
const displayStats = computed(() => {
  if (props.stats.length) return props.stats
  if (props.variant === 'normal' && props.kb) {
    return [String(props.kb.doc_count), String(props.kb.vector_count)]
  }
  return []
})

function onSelect() {
  if (props.disabled) return
  emit('select', props.selectId)
}
</script>

<template>
  <AppCard
    class="kb-pick-card"
    :active="active"
    :disabled="disabled"
    role="button"
    tabindex="0"
    @click="onSelect"
    @keydown.enter.prevent="onSelect"
    @keydown.space.prevent="onSelect"
  >
    <div class="kb-pick-inner">
      <div class="kb-pick-avatar" :class="avatarClass">{{ avatarIcon }}</div>
      <div class="kb-pick-body">
        <strong class="kb-pick-name">{{ displayName }}</strong>
        <span v-if="displayDesc" class="kb-pick-desc">{{ displayDesc }}</span>
        <div v-if="displayStats.length" class="kb-pick-stats">
          <span v-for="(s, i) in displayStats" :key="i" class="kb-pick-chip">{{ s }}</span>
        </div>
        <div v-if="variant === 'all' && softChip" class="kb-pick-stats">
          <span class="kb-pick-chip kb-pick-chip-soft">{{ softChip }}</span>
        </div>
        <span v-if="variant === 'all' && meta" class="kb-pick-meta">{{ meta }}</span>
      </div>
    </div>
  </AppCard>
</template>

<style scoped>
.kb-pick-card {
  /* inherits AppCard's active/hover/keyboard states */
}
.kb-pick-inner { display: flex; align-items: flex-start; gap: 10px; }
.kb-pick-avatar {
  flex-shrink: 0;
  width: 36px; height: 36px;
  border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  font-size: 18px;
  background: var(--color-primary-soft);
}
.kb-pick-avatar-all { background: var(--color-border); }
.kb-pick-body { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 4px; }
.kb-pick-name { font-size: 14px; font-weight: 600; color: var(--color-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-pick-desc { font-size: var(--text-xs); color: var(--color-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kb-pick-stats { display: flex; flex-wrap: wrap; gap: 6px; }
.kb-pick-chip {
  font-size: 0.7rem; line-height: 1.4;
  color: var(--color-text-muted);
  background: var(--color-surface-2, #f1f5f9);
  border: 1px solid var(--color-border);
  border-radius: 9999px;
  padding: 1px 8px;
}
.kb-pick-chip-soft { color: var(--color-primary); background: var(--color-primary-soft); border-color: transparent; }
.kb-pick-meta { font-size: 0.7rem; color: var(--color-text-muted); }
</style>

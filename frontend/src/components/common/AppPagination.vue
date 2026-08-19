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
<template>
  <div v-if="shouldShow" class="app-pagination" :class="`app-pagination--${align}`">
    <NPagination
      :page="page"
      :page-size="pageSize"
      :item-count="itemCount"
      :page-count="pageCount"
      :page-slot="pageSlot"
      :simple="simple"
      :show-size-picker="showSizePicker"
      :page-sizes="pageSizes"
      @update:page="(p: number) => emit('update:page', p)"
      @update:page-size="(s: number) => emit('update:pageSize', s)"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NPagination } from 'naive-ui'

const props = withDefaults(
  defineProps<{
    /** Current page (v-model:page) */
    page: number
    /** Items per page */
    pageSize?: number
    /** Total item count */
    itemCount?: number
    /** Total page count (alternative to itemCount; takes precedence over itemCount for visibility) */
    pageCount?: number
    /** Number of page-number buttons */
    pageSlot?: number
    /** Simple mode */
    simple?: boolean
    /** Show the "items per page" selector */
    showSizePicker?: boolean
    /** Optional items-per-page choices */
    pageSizes?: number[]
    /** Alignment: center (default) / end (right-aligned) */
    align?: 'center' | 'end'
    /** Render even when there is only one page (for lists with a size-picker that still need to show the per-page selector) */
    alwaysShow?: boolean
  }>(),
  {
    pageSize: 20,
    // Must be undefined, not 0. Naive's NPagination, when itemCount !== undefined,
    // derives the page count from itemCount (mergedPageCountRef) first, and 0 is treated as "real total 0 → 1 page",
    // thus ignoring the caller-supplied :page-count. Only the default undefined takes the pageCount branch.
    itemCount: undefined,
    pageSlot: 7,
    simple: false,
    showSizePicker: false,
    pageSizes: () => [20, 50, 100],
    align: 'center',
    alwaysShow: false,
  },
)

const emit = defineEmits<{
  'update:page': [page: number]
  'update:pageSize': [size: number]
}>()

// Show only when there is more than one page; alwaysShow is for lists with a size-picker (a single page still needs to show the per-page switcher)
const shouldShow = computed(() =>
  props.alwaysShow ||
  (props.pageCount != null
    ? props.pageCount > 1
    : (props.itemCount ?? 0) > props.pageSize),
)
</script>

<style scoped>
/* Unified pagination bar: visually consistent with the main list page (centered, uniform vertical spacing) */
.app-pagination {
  display: flex;
  margin-top: 16px;
  padding-bottom: 24px;
}
.app-pagination--center {
  justify-content: center;
}
.app-pagination--end {
  justify-content: flex-end;
}
</style>

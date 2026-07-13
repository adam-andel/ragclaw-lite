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
    /** 当前页（v-model:page） */
    page: number
    /** 每页条数 */
    pageSize?: number
    /** 总条数 */
    itemCount?: number
    /** 总页数（与 itemCount 二选一；优先于 itemCount 判断显隐） */
    pageCount?: number
    /** 页码按钮数量 */
    pageSlot?: number
    /** 简洁模式 */
    simple?: boolean
    /** 显示“每页条数”选择器 */
    showSizePicker?: boolean
    /** 可选每页条数 */
    pageSizes?: number[]
    /** 对齐方式：center 居中（默认）/ end 右对齐 */
    align?: 'center' | 'end'
    /** 仅一页时也渲染（用于带 size-picker 的列表，单页也需展示切换每页条数） */
    alwaysShow?: boolean
  }>(),
  {
    pageSize: 20,
    // 必须是 undefined 而非 0。Naive 的 NPagination 在 itemCount !== undefined 时
    // 优先用 itemCount 推导页数（mergedPageCountRef），0 会被当成“真实总数 0 → 1 页”，
    // 从而忽略调用方单独传入的 :page-count。默认 undefined 时才会走 pageCount 分支。
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

// 多于 1 页才显示；alwaysShow 用于带 size-picker 的列表（单页也需展示切换每页条数）
const shouldShow = computed(() =>
  props.alwaysShow ||
  (props.pageCount != null
    ? props.pageCount > 1
    : (props.itemCount ?? 0) > props.pageSize),
)
</script>

<style scoped>
/* 统一分页条：与主列表页视觉一致（居中、统一上下间距） */
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

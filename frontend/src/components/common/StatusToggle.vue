<script setup lang="ts">
import { NSwitch } from 'naive-ui'

withDefaults(
  defineProps<{
    value: boolean
    disabled?: boolean
    loading?: boolean
    size?: 'small' | 'medium' | 'large'
    checkedText?: string
    uncheckedText?: string
  }>(),
  {
    disabled: false,
    loading: false,
    size: 'small',
    checkedText: '启用',
    uncheckedText: '禁用',
  },
)

const emit = defineEmits<{
  (e: 'update:value', value: boolean): void
}>()
</script>

<template>
  <div class="st-toggle">
    <NSwitch
      :value="value"
      :disabled="disabled"
      :loading="loading"
      :size="size"
      @update:value="(v: boolean) => emit('update:value', v)"
    >
      <template #checked>{{ checkedText }}</template>
      <template #unchecked>{{ uncheckedText }}</template>
    </NSwitch>
  </div>
</template>

<style scoped>
.st-toggle {
  display: inline-flex;
}
/* 启用态（on）固定使用绿色（#22c55e），与项目 success 语义色一致。
   刻意写死为常量而非引用主题变量：避免主题主色被改（如改成红色）时，
   启用开关跟着变红，导致"启用=红色"的语义误导。
   注意：Naive 把 --n-rail-color-active 作为内联 style 绑在 .n-switch 根元素上
   （见 naive-ui Switch.mjs:282 style=this.cssVars），内联样式优先级高于普通选择器，
   因此必须用 !important 才能盖过它。 */
.st-toggle :deep(.n-switch) {
  --n-rail-color-active: #22c55e !important;
}
/* 启用/禁用 文字字号略缩小 */
.st-toggle :deep(.n-switch__checked),
.st-toggle :deep(.n-switch__unchecked) {
  font-size: 11px;
}
</style>

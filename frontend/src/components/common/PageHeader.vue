<script setup lang="ts">
import { NIcon } from 'naive-ui'
import type { Component } from 'vue'

withDefaults(
  defineProps<{
    /** 标题文字（必填） */
    title?: string
    /** 标题语义标签，默认 h2 */
    titleTag?: 'h1' | 'h2' | 'h3'
    /** 副标题（仅系统设置页用），也可用 #subtitle 插槽 */
    subtitle?: string
    /** 左侧图标组件，传入即用主色 22px 渲染；也可用 #icon 插槽自定义 */
    icon?: Component
  }>(),
  {
    title: '',
    titleTag: 'h2',
    subtitle: '',
  },
)
</script>

<template>
  <div class="page-header">
    <div class="ph-left">
      <span v-if="icon" class="ph-icon">
        <NIcon size="22" color="var(--color-primary)"><component :is="icon" /></NIcon>
      </span>
      <span v-else-if="$slots.icon" class="ph-icon"><slot name="icon" /></span>

      <div class="ph-titles">
        <component :is="titleTag" class="view-title">{{ title }}</component>
        <p v-if="subtitle || $slots.subtitle" class="page-subtitle">
          <slot name="subtitle">{{ subtitle }}</slot>
        </p>
      </div>

      <span v-if="$slots.badge" class="ph-badge"><slot name="badge" /></span>
    </div>

    <div v-if="$slots.actions" class="ph-actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: 16px 20px;
  margin-bottom: 20px;
  background: linear-gradient(135deg, var(--color-primary-soft), transparent);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
  flex-shrink: 0;
}
.ph-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.ph-icon {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
}
.ph-titles {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.page-subtitle {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  margin: 2px 0 0;
}
.ph-badge {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-primary);
  background: var(--color-primary-soft);
  padding: 2px 10px;
  border-radius: var(--radius-full);
  border: 1px solid var(--color-primary);
  flex-shrink: 0;
}
.ph-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

@media (max-width: 767px) {
  .page-header {
    flex-wrap: wrap;
    padding: 10px 14px;
    gap: 6px;
  }
  .page-header .view-title {
    font-size: var(--text-base);
  }
  .ph-actions {
    flex-wrap: wrap;
    gap: 4px;
    width: 100%;
    justify-content: flex-end;
  }
}
</style>

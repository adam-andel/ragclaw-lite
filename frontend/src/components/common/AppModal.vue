<script setup lang="ts">
import { computed, useAttrs, defineOptions } from 'vue'
import { NModal } from 'naive-ui'

// 防止父级把 style/class/preset 透传到 NModal 根，确保尺寸由本组件独家控制
defineOptions({ inheritAttrs: false })

export type AppModalSize = 'detail' | 'nested' | 'wide' | 'code'

const props = withDefaults(
  defineProps<{
    show?: boolean
    size?: AppModalSize
    title?: string
  }>(),
  {
    show: false,
    size: 'detail',
    title: '',
  },
)

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
}>()

const attrs = useAttrs()

// 模态尺寸唯一来源（层级而非用途）：
//   detail 一级·卡片详情（统一 560）
//   nested 详情内嵌套（必须 < detail，480）
//   wide   列表/选择器/引用（720）
//   code   代码编辑器（800）
const SIZE_WIDTH: Record<AppModalSize, string> = {
  detail: '560px',
  nested: '480px',
  wide: '720px',
  code: '800px',
}

const modalStyle = computed(() => ({
  width: '90vw',
  maxWidth: SIZE_WIDTH[props.size],
  maxHeight: '85vh',
}))

// 剥离 style/preset（保护尺寸与 preset=card 不被父级覆盖），但把标记类 app-modal
// 合并进 class 透传下去——这是滚动/页脚固定样式（全局 .app-modal .n-card）的挂载点。
// 注意：不能把 class 整个丢弃，否则 .app-modal 标记丢失、全局规则无法命中（之前因此失效）。
const modalAttrs = computed(() => {
  const { style: _style, preset: _preset, class: cls, ...rest } = attrs as Record<string, unknown>
  return {
    ...rest,
    class: ['app-modal', cls].filter(Boolean).join(' '),
  }
})
</script>

<template>
  <NModal
    v-bind="modalAttrs"
    :show="show"
    preset="card"
    :title="title"
    :style="modalStyle"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <slot />
    <template v-if="$slots.header" #header>
      <slot name="header" />
    </template>
    <template v-if="$slots.footer" #footer>
      <slot name="footer" />
    </template>
    <template v-if="$slots.action" #action>
      <slot name="action" />
    </template>
    <template v-if="$slots.close" #close>
      <slot name="close" />
    </template>
  </NModal>
</template>

<!-- 注意：必须为非 scoped（全局）样式。Naive 的 NModal 会 teleport 到 <body>，
     若写成 scoped + :deep，规则无法穿透 teleport 子树，导致内容仍会撑破 modal。
     这里用普通全局选择器，确保 teleport 后依然生效。 -->
<style>
/* 统一 modal 内部布局：卡片为纵向 flex 列，内容区滚动、页脚固定在底部。
   这样无论哪个页面（如 CronJobsView 详情 15 行 / 表单 8 项、SkillsView 编辑 SKILL.md）
   内容超高，都不会撑破 modal 框，底部操作按钮也始终可见。

   关键：preset="card" 时 Naive 把本组件透传的 class 直接挂在 .n-card 根元素上
   （见 naive-ui BodyWrapper.mjs：h(NCard, { class:[n-modal, $attrs.class] })），
   所以 .app-modal 与 .n-card 是【同一个元素】——必须用【复合选择器】
   `.app-modal.n-card` 命中弹窗卡片本身；内容区用【直接子选择器】`> .n-card-content`。
   切勿用后代选择器 `.app-modal .n-card` / `.app-modal .n-card-content`：那样会误命中
   弹窗内的【嵌套 NCard】（如分块预览里每页 10 张 chunk 卡片），把每张都变成
   max-height:85vh 的独立滚动容器，导致关闭动画期间大量多余布局/合成、卡顿变慢。 */
.app-modal.n-card {
  display: flex;
  flex-direction: column;
  max-height: 85vh;
}
/* 注意：Naive 卡片内容区类名是 `n-card-content`（单连词，非 BEM 双下划线
   `n-card__content`）。用直接子选择器只作用于弹窗自身内容区，不波及嵌套卡片。 */
.app-modal.n-card > .n-card-content {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}
</style>

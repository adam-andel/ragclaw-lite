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
import { computed, useAttrs, defineOptions } from 'vue'
import { NModal } from 'naive-ui'

// Prevent the parent from passing style/class/preset through to the NModal root, so sizing is controlled solely by this component
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

// Single source of truth for modal size (by tier, not by purpose):
//   detail  — top tier, card detail (uniform 560)
//   nested  — nested inside detail (must be < detail, 480)
//   wide    — list / picker / reference (720)
//   code    — code editor (800)
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

// Strip style/preset (protect sizing and preset=card from being overridden by the parent), but keep the marker class app-modal
// merged back into the class passed down — this is the mount point for the scroll/footer-pinning styles (global .app-modal .n-card).
// Note: the class must not be dropped entirely, otherwise the .app-modal marker is lost and the global rules can no longer match (they broke for this reason before).
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
    :auto-focus="false"
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

<!-- Note: this must be non-scoped (global) styles. Naive's NModal teleports to <body>,
     so if written as scoped + :deep, the rules cannot penetrate the teleported subtree and content would still overflow the modal.
     Here we use plain global selectors to ensure they still apply after teleport. -->
<style>
/* Unified modal inner layout: the card is a vertical flex column, the content area scrolls, and the footer is pinned to the bottom.
   This way, on any page (e.g. CronJobsView details with 15 rows / 8 form fields, SkillsView editing SKILL.md)
   with oversized content, the modal frame is never overflowed and the bottom action buttons stay visible.

   Key point: with preset="card", Naive mounts the class passed through by this component directly onto the .n-card root element
   (see naive-ui BodyWrapper.mjs: h(NCard, { class: [n-modal, $attrs.class] })),
   so .app-modal and .n-card are the SAME element — we must use a COMPOUND selector
   `.app-modal.n-card` to target the modal card itself; for the content area use the DIRECT-CHILD selector `> .n-card-content`.
   Do NOT use descendant selectors `.app-modal .n-card` / `.app-modal .n-card-content`: that would accidentally match
   NESTED NCards inside the modal (e.g. the 10 chunk cards per page in the chunk preview), turning each into its own
   max-height:85vh scroll container, causing excessive layout/compositing and jank during the close animation. */
.app-modal.n-card {
  display: flex;
  flex-direction: column;
  max-height: 85vh;
}
/* Note: Naive's card content class is `n-card-content` (single word, not the BEM double-underscore
   `n-card__content`). Using the direct-child selector scopes this to the modal's own content area without affecting nested cards. */
.app-modal.n-card > .n-card-content {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}
/* Smaller modal title. The title (from the `title` prop) is rendered inside
   .n-card-header__main; scope it to .app-modal so only these modals are affected. */
.app-modal.n-card > .n-card-header .n-card-header__main {
  font-size: 1rem;
}
/* Faster open/close transition.
   IMPORTANT: in naive-ui the fade-in-scale-up transition classes are applied to the `.n-modal-body-wrapper`
   (the <Transition> root), NOT to the `.n-card`. So the previous rule that only targeted
   `.app-modal.n-card.fade-in-scale-up-transition-*` did NOT control the actual close/open fade+scale —
   it only affected the card's own border/box-shadow transition, leaving the visible close animation at
   naive-ui's default (laggy) duration. We now target the body wrapper (where the real transition runs) and
   the backdrop mask, with a short duration and zero delay. !important guarantees it beats naive-ui's runtime cssr.
   Nested NCards inside the modal are untouched (they don't carry these transition classes on their root). */
.n-modal-body-wrapper.fade-in-scale-up-transition-enter-active,
.n-modal-body-wrapper.fade-in-scale-up-transition-leave-active,
.app-modal.n-card.fade-in-scale-up-transition-enter-active,
.app-modal.n-card.fade-in-scale-up-transition-leave-active {
  transition-duration: 0.08s !important;
  transition-delay: 0s !important;
}
.n-modal-mask {
  transition-duration: 0.08s !important;
  transition-delay: 0s !important;
}
</style>

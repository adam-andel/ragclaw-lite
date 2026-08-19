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
 * Shared card box.
 * Handles ONLY size / border / background / hover / focus / disabled / active states.
 * Content layout lives in callers (default + #footer slots).
 *
 * Callers' extra semantic classes (e.g. `cj-card`) are inherited onto the
 * NCard root node via Vue's inheritAttrs, so their `:deep(.n-card__footer)`
 * and descendant `:hover` rules keep working.
 */
import { NCard } from 'naive-ui'

withDefaults(
  defineProps<{
    disabled?: boolean
    active?: boolean
    clickable?: boolean
  }>(),
  {
    disabled: false,
    active: false,
    clickable: true,
  },
)

defineSlots<{
  default?: () => unknown
  footer?: () => unknown
}>()
</script>

<template>
  <NCard
    size="small"
    :class="[
      'app-card',
      { 'app-card--disabled': disabled, 'app-card--active': active, 'app-card--static': !clickable },
    ]"
  >
    <template v-if="$slots.default" #default>
      <slot />
    </template>
    <template v-if="$slots.footer" #footer>
      <slot name="footer" />
    </template>
  </NCard>
</template>

<style scoped>
.app-card {
  background: var(--color-card-bg);
  --n-color: var(--color-card-bg);
  border: 1px solid var(--color-card-border);
  --n-border-color: var(--color-card-border);
  box-shadow: var(--shadow-sm);
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease, transform 0.15s ease;
}
.app-card:hover {
  border-color: var(--color-primary);
  box-shadow: var(--shadow);
  transform: translateY(-1px);
}
.app-card:focus-visible {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-soft);
}
.app-card--disabled {
  background: var(--color-card-bg-disabled);
  --n-color: var(--color-card-bg-disabled);
  cursor: not-allowed;
}
.app-card--disabled:hover {
  border-color: var(--color-card-border);
  box-shadow: var(--shadow-sm);
  transform: none;
}
.app-card--active {
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
}
.app-card {
  cursor: pointer;
}
.app-card--static {
  cursor: default;
}
</style>

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
import { computed } from 'vue'
import { NSwitch } from 'naive-ui'
import { useI18n } from 'vue-i18n'

const props = withDefaults(
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
    checkedText: '',
    uncheckedText: '',
  },
)

const { t } = useI18n()

// Resolve display labels in setup scope (defineProps defaults cannot reference
// locally-declared variables like `t`, because the macro is hoisted out of setup).
const resolvedCheckedText = computed(() => props.checkedText || t('common.enable'))
const resolvedUncheckedText = computed(() => props.uncheckedText || t('common.disable'))

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
      <template #checked>{{ resolvedCheckedText }}</template>
      <template #unchecked>{{ resolvedUncheckedText }}</template>
    </NSwitch>
  </div>
</template>

<style scoped>
.st-toggle {
  display: inline-flex;
}
/* The "on" state is hardcoded to green (#22c55e) to match the project's success semantic color.
   It is intentionally a hardcoded constant rather than a theme variable: this prevents the toggle from turning red
   if the theme's primary color is changed (e.g. to red), which would misleadingly imply "enabled = red".
   Note: Naive binds --n-rail-color-active as an inline style on the .n-switch root element
   (see naive-ui Switch.mjs:282 style=this.cssVars); inline styles outrank normal selectors,
   so !important is required to override it. */
.st-toggle :deep(.n-switch) {
  --n-rail-color-active: #22c55e !important;
}
/* Slightly reduce the font size of the enabled/disabled label */
.st-toggle :deep(.n-switch__checked),
.st-toggle :deep(.n-switch__unchecked) {
  font-size: 11px;
}
</style>

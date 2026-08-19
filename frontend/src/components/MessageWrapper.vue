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
// This component lives *inside* <NMessageProvider>, so useMessage()
// resolves to the single shared Naive UI message API instance that every
// `useMessage()` call across the app returns. We mutate that shared
// instance in place so all consumers (AppLayout + every view) get
// `closable: true` by default — no per-call changes anywhere.
//
// Global duration is set on <NMessageProvider :duration> in App.vue, so a
// per-call `duration` (e.g. loading toasts with duration: 0) still wins
// via the spread in each wrapper below.
import { useMessage } from 'naive-ui'

const message = useMessage()

type MsgMethod = (content: unknown, options?: Record<string, unknown>) => unknown
const api = message as unknown as Record<string, MsgMethod>

for (const name of ['success', 'error', 'warning', 'info', 'loading', 'create'] as const) {
  const original = api[name]
  api[name] = (content: unknown, options?: Record<string, unknown>) =>
    original(content, { closable: true, ...options })
}
// `destroyAll` takes no content/options — left untouched.
</script>

<template>
  <slot />
</template>

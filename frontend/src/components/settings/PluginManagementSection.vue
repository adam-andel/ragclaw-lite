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
import { ref, computed, onMounted } from 'vue'
import { backendErrorMessage } from '@/utils/backendError'
import { useI18n } from 'vue-i18n'
import {
  NCard, NButton, NSwitch, NTag, NSpace, NSpin, NEmpty,
  NIcon, NInput, useMessage, NGrid, NGridItem, NText, NTooltip,
} from 'naive-ui'
import {
  listPlugins, enablePlugin, disablePlugin, refreshPluginCache,
} from '@/api/plugins'
import type { PluginInfo } from '@/types'

const { t } = useI18n()
const emit = defineEmits(['stats'])
const message = useMessage()

const plugins = ref<PluginInfo[]>([])
const loading = ref(false)
const toggling = ref<string | null>(null)
const disableReason = ref<Record<string, string>>({})

const categoryColors: Record<string, string> = {
  office: 'blue',
  data: 'amber',
  web: 'cyan',
  email: 'teal',
  ebook: 'green',
  text: 'default',
  notebook: 'orange',
}

const categoryLabels: Record<string, string> = {
  office: 'plugins.category.office',
  data: 'plugins.category.data',
  web: 'plugins.category.web',
  email: 'plugins.category.email',
  ebook: 'plugins.category.ebook',
  text: 'plugins.category.text',
  notebook: 'plugins.category.notebook',
}

async function load() {
  loading.value = true
  try {
    const data = await listPlugins()
    plugins.value = data.items
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('plugins.msg.loadFailed'))
  } finally {
    loading.value = false
    emit('stats', { enabled: enabledCount.value, total: plugins.value.length, loading: false })
  }
}

async function handleToggle(name: string, enabled: boolean) {
  toggling.value = name
  try {
    if (enabled) {
      await enablePlugin(name)
      message.success(t('plugins.msg.enabled'))
    } else {
      const reason = disableReason.value[name]?.trim()
      await disablePlugin(name, reason || undefined)
      message.success(t('plugins.msg.disabled'))
      disableReason.value[name] = ''
    }
    await load()
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('plugins.msg.opFailed'))
  } finally {
    toggling.value = null
  }
}

async function handleRefreshCache() {
  try {
    await refreshPluginCache()
    message.success(t('plugins.msg.refreshSuccess'))
    await load()
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('plugins.msg.refreshFailed'))
  }
}

function formatExts(exts: string[]): string {
  return exts.map(e => `.${e}`).join(' / ')
}

function formatTime(tm: string | null): string {
  if (!tm) return '-'
  return tm.slice(0, 16).replace('T', ' ')
}

const enabledCount = computed(() => plugins.value.filter(p => p.enabled).length)

onMounted(load)

defineExpose({ refresh: handleRefreshCache })
</script>

<template>
  <div class="plugin-section">
    <NSpin :show="loading">
      <NEmpty v-if="!loading && plugins.length === 0" :description="t('plugins.empty')" />
      <NGrid v-else :cols="2" :x-gap="12" :y-gap="12" responsive="screen" item-responsive>
        <NGridItem
          v-for="plugin in plugins"
          :key="plugin.name"
          span="2 m:1"
        >
          <NCard size="small" :class="['plugin-card', { disabled: !plugin.enabled }]">
            <template #header>
              <div class="plugin-header">
                <NSpace align="center" :size="8">
                  <NText strong>{{ t('plugins.' + plugin.display_name) }}</NText>
                  <NTag size="tiny" :type="(categoryColors[plugin.category] || 'default') as any">
                    {{ categoryLabels[plugin.category] ? t(categoryLabels[plugin.category]) : plugin.category }}
                  </NTag>
                  <NText depth="3" style="font-size:12px">v{{ plugin.version }}</NText>
                </NSpace>
                <NSwitch
                  :value="plugin.enabled"
                  :loading="toggling === plugin.name"
                  @update:value="(val: boolean) => handleToggle(plugin.name, val)"
                />
              </div>
            </template>

            <div class="plugin-body">
              <p class="plugin-desc">{{ t('plugins.' + plugin.description) }}</p>
              <div class="plugin-exts">
                <NText depth="3" style="font-size:12px">{{ t('plugins.supportFormats') }}</NText>
                <NText code style="font-size:12px">{{ formatExts(plugin.extensions) }}</NText>
              </div>

              <div v-if="!plugin.enabled" class="plugin-disabled-info">
                <NSpace :size="12" align="center">
                  <NTag size="tiny" type="error">{{ t('common.disabled') }}</NTag>
                  <NText depth="3" style="font-size:12px">
                    {{ t('plugins.disabledBy') }}{{ plugin.disabled_by ? plugin.disabled_by.slice(0, 8) : '-' }} · {{ formatTime(plugin.disabled_at) }}
                  </NText>
                </NSpace>
                <p v-if="plugin.reason" class="plugin-reason">{{ t('plugins.reason') }}{{ plugin.reason }}</p>

                <div class="disable-reason-input">
                  <NInput
                    v-model:value="disableReason[plugin.name]"
                    size="tiny"
                    :placeholder="t('plugins.disabledReasonPlaceholder')"
                    :disabled="toggling === plugin.name"
                  />
                </div>
              </div>

              <div v-else-if="disableReason[plugin.name] !== undefined" class="disable-reason-input">
                <NTooltip placement="top">
                  <template #trigger>
                    <NInput
                      v-model:value="disableReason[plugin.name]"
                      size="tiny"
                      :placeholder="t('plugins.disableReasonPlaceholder')"
                      :disabled="toggling === plugin.name"
                    />
                  </template>
                  {{ t('plugins.disableReasonTooltip') }}
                </NTooltip>
              </div>
            </div>
          </NCard>
        </NGridItem>
      </NGrid>
    </NSpin>
  </div>
</template>

<style scoped>
.plugin-section { width: 100%; }
.section-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.section-title h3 {
  margin: 0;
  font-size: var(--text-base);
  font-weight: 600;
}
.plugin-card.disabled {
  opacity: 0.75;
}
.plugin-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.plugin-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.plugin-desc {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}
.plugin-exts {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}
.plugin-disabled-info {
  margin-top: var(--space-1);
  padding-top: var(--space-2);
  border-top: 1px dashed var(--color-border);
}
.plugin-reason {
  margin: var(--space-1) 0 0;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.disable-reason-input {
  margin-top: var(--space-2);
}
</style>

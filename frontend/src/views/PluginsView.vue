<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  NCard, NButton, NSwitch, NTag, NSpace, NSpin, NEmpty,
  NIcon, NInput, useMessage, NGrid, NGridItem, NText, NTooltip,
} from 'naive-ui'
import { Refresh, ExtensionPuzzle } from '@vicons/ionicons5'
import {
  listPlugins, enablePlugin, disablePlugin, refreshPluginCache,
} from '@/api/plugins'
import type { PluginInfo } from '@/types'

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
  office: '办公文档',
  data: '数据',
  web: '网页',
  email: '邮件',
  ebook: '电子书',
  text: '文本',
  notebook: '笔记本',
}

async function load() {
  loading.value = true
  try {
    const data = await listPlugins()
    plugins.value = data.items
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || '加载插件列表失败')
  } finally {
    loading.value = false
  }
}

async function handleToggle(name: string, enabled: boolean) {
  toggling.value = name
  try {
    if (enabled) {
      await enablePlugin(name)
      message.success('插件已启用')
    } else {
      const reason = disableReason.value[name]?.trim()
      await disablePlugin(name, reason || undefined)
      message.success('插件已禁用')
      disableReason.value[name] = ''
    }
    await load()
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || '操作失败')
  } finally {
    toggling.value = null
  }
}

async function handleRefreshCache() {
  try {
    await refreshPluginCache()
    message.success('缓存已刷新')
    await load()
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e.message || '刷新失败')
  }
}

function formatExts(exts: string[]): string {
  return exts.map(e => `.${e}`).join(' / ')
}

function formatTime(t: string | null): string {
  if (!t) return '-'
  return t.slice(0, 16).replace('T', ' ')
}

const enabledCount = computed(() => plugins.value.filter(p => p.enabled).length)

onMounted(load)
</script>

<template>
  <div class="page-container">
    <NCard size="small">
      <template #header>
        <div class="page-header">
          <div class="page-title">
            <NIcon size="20" color="var(--color-primary)"><ExtensionPuzzle /></NIcon>
            <h2>插件管理</h2>
            <NTag v-if="!loading" size="small" type="info">
              {{ enabledCount }}/{{ plugins.length }} 启用
            </NTag>
          </div>
          <NButton size="small" secondary @click="handleRefreshCache">
            <template #icon><NIcon><Refresh /></NIcon></template>
            刷新缓存
          </NButton>
        </div>
      </template>

      <NSpin :show="loading">
        <NEmpty v-if="!loading && plugins.length === 0" description="暂无插件" />
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
                    <NText strong>{{ plugin.display_name }}</NText>
                    <NTag size="tiny" :type="(categoryColors[plugin.category] || 'default') as any">
                      {{ categoryLabels[plugin.category] || plugin.category }}
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
                <p class="plugin-desc">{{ plugin.description }}</p>
                <div class="plugin-exts">
                  <NText depth="3" style="font-size:12px">支持格式：</NText>
                  <NText code style="font-size:12px">{{ formatExts(plugin.extensions) }}</NText>
                </div>

                <div v-if="!plugin.enabled" class="plugin-disabled-info">
                  <NSpace :size="12" align="center">
                    <NTag size="tiny" type="error">已禁用</NTag>
                    <NText depth="3" style="font-size:12px">
                      禁用人：{{ plugin.disabled_by ? plugin.disabled_by.slice(0, 8) : '-' }} · {{ formatTime(plugin.disabled_at) }}
                    </NText>
                  </NSpace>
                  <p v-if="plugin.reason" class="plugin-reason">原因：{{ plugin.reason }}</p>

                  <div class="disable-reason-input">
                    <NInput
                      v-model:value="disableReason[plugin.name]"
                      size="tiny"
                      placeholder="修改禁用原因（可选，启用后清除）"
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
                        placeholder="禁用原因（可选）"
                        :disabled="toggling === plugin.name"
                      />
                    </template>
                    关闭开关时将提交此原因
                  </NTooltip>
                </div>
              </div>
            </NCard>
          </NGridItem>
        </NGrid>
      </NSpin>
    </NCard>
  </div>
</template>

<style scoped>
.page-container {
  padding: var(--space-4);
  max-width: 1100px;
  margin: 0 auto;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.page-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.page-title h2 {
  margin: 0;
  font-size: var(--text-lg);
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

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  NForm, NFormItem, NInput, NButton, NSelect, NSlider, NInputNumber,
  NCard, NIcon, useMessage, NAlert, NSpace, NDivider,
} from 'naive-ui'
import { Settings, Save, Flash, Key, Globe, AlertCircle, CheckmarkCircle } from '@vicons/ionicons5'
import { getLLMConfig, updateLLMConfig, testLLMConnection, type LLMConfig } from '@/api/settings'

const message = useMessage()

const providerOptions = [
  { label: 'OpenAI', value: 'openai' },
  { label: 'Qwen (通义千问)', value: 'qwen' },
  { label: 'Ollama (本地)', value: 'ollama' },
  { label: '自定义', value: 'custom' },
]

const urlDefaults: Record<string, string> = {
  openai: 'https://api.openai.com/v1',
  qwen: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  ollama: 'http://localhost:11434/v1',
  custom: '',
}

const config = ref<LLMConfig>({
  llm_provider: 'openai', llm_model: '', llm_api_key: '',
  llm_base_url: '', llm_temperature: 0.3, llm_max_tokens: 2048,
  is_configured: false,
})

const apiKeyInput = ref('')
const saving = ref(false)
const testing = ref(false)
const testResult = ref<{ ok: boolean; text: string } | null>(null)

onMounted(async () => {
  try {
    config.value = await getLLMConfig()
  } catch (e: any) {
    message.error(e.message || '加载配置失败')
  }
})

function clearTest() { testResult.value = null }

function onProviderChange(val: string) {
  // Auto-fill default base_url when switching providers
  const defaultUrl = urlDefaults[val] ?? ''
  if (defaultUrl && (!config.value.llm_base_url || Object.values(urlDefaults).includes(config.value.llm_base_url))) {
    config.value.llm_base_url = defaultUrl
  }
}

async function handleSave() {
  saving.value = true
  try {
    const payload: Record<string, any> = {
      llm_provider: config.value.llm_provider,
      llm_model: config.value.llm_model,
      llm_base_url: config.value.llm_base_url,
      llm_temperature: config.value.llm_temperature,
      llm_max_tokens: config.value.llm_max_tokens,
    }
    if (apiKeyInput.value.trim()) {
      payload.llm_api_key = apiKeyInput.value.trim()
    }
    const res = await updateLLMConfig(payload)
    config.value = res.config
    apiKeyInput.value = ''
    testResult.value = null
    message.success('配置已保存，立即生效')
  } catch (e: any) {
    message.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleTest() {
  testing.value = true
  testResult.value = null
  try {
    const res = await testLLMConnection()
    testResult.value = res.ok
      ? { ok: true, text: `✅ 连接成功 — 模型: ${res.model}，回复: "${res.reply}"` }
      : { ok: false, text: `❌ 连接失败: ${res.error}` }
  } catch (e: any) {
    testResult.value = { ok: false, text: `❌ 请求异常: ${e.message}` }
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <div class="settings-page">
    <div class="settings-header">
      <h1 class="page-title">系统设置</h1>
      <p class="page-subtitle">LLM 配置 · 仅超级管理员可访问</p>
    </div>

    <!-- 未配置警告 -->
    <NAlert
      v-if="!config.is_configured"
      type="warning"
      title="尚未配置 LLM API Key"
      :bordered="false"
      style="margin-bottom: 16px"
    >
      <template #icon><NIcon :component="AlertCircle" /></template>
      请先录入 LLM 服务商的 API Key，否则系统无法进行对话。录入后立即生效，无需重启。
    </NAlert>

    <NCard :bordered="false" class="settings-card">
      <NForm label-placement="left" label-width="120">

        <!-- Provider -->
        <NFormItem label="LLM 提供商">
          <NSelect
            v-model:value="config.llm_provider"
            :options="providerOptions"
            @update:value="onProviderChange"
          />
        </NFormItem>

        <!-- API Key -->
        <NFormItem label="API Key" :required="!config.is_configured">
          <NInput
            v-model:value="apiKeyInput"
            type="password"
            show-password-on="click"
            :placeholder="config.is_configured ? `当前: ${config.llm_api_key}（留空不修改）` : '请输入 API Key（首次录入）'"
            maxlength="512"
            @input="clearTest"
          >
            <template #prefix><NIcon :component="Key" /></template>
          </NInput>
        </NFormItem>

        <!-- Base URL -->
        <NFormItem label="Base URL">
          <NInput v-model:value="config.llm_base_url" placeholder="https://api.openai.com/v1" @input="clearTest">
            <template #prefix><NIcon :component="Globe" /></template>
          </NInput>
        </NFormItem>

        <!-- Model -->
        <NFormItem label="模型名称">
          <NInput v-model:value="config.llm_model" placeholder="gpt-4o-mini" @input="clearTest">
            <template #prefix><NIcon :component="Settings" /></template>
          </NInput>
        </NFormItem>

        <!-- Temperature -->
        <NFormItem label="Temperature">
          <NSpace align="center">
            <NSlider v-model:value="config.llm_temperature" :min="0" :max="2" :step="0.05" style="width: 200px" @update:value="clearTest" />
            <span class="slider-value">{{ config.llm_temperature.toFixed(2) }}</span>
          </NSpace>
        </NFormItem>

        <!-- Max Tokens -->
        <NFormItem label="Max Tokens">
          <NInputNumber v-model:value="config.llm_max_tokens" :min="128" :max="131072" :step="256" @update:value="clearTest" />
        </NFormItem>

        <NDivider />

        <!-- 测试结果 -->
        <div v-if="testResult" :class="['test-result', testResult.ok ? 'test-ok' : 'test-fail']">
          <NIcon :component="testResult.ok ? CheckmarkCircle : AlertCircle" size="16" />
          <span>{{ testResult.text }}</span>
        </div>

        <!-- 操作按钮 -->
        <NFormItem :show-feedback="false">
          <NSpace>
            <NButton
              type="info"
              :loading="testing"
              :disabled="!apiKeyInput.trim() && !config.is_configured"
              @click="handleTest"
            >
              <template #icon><NIcon :component="Flash" /></template>
              测试连接
            </NButton>
            <NButton
              type="primary"
              :loading="saving"
              :disabled="!apiKeyInput.trim() && !config.is_configured"
              @click="handleSave"
            >
              <template #icon><NIcon :component="Save" /></template>
              保存配置
            </NButton>
          </NSpace>
        </NFormItem>
      </NForm>
    </NCard>
  </div>
</template>

<style scoped>
.settings-page { max-width: 640px; margin: 0 auto; }
.settings-header { margin-bottom: var(--space-6); }
.page-title { font-size: var(--text-2xl); font-weight: 700; color: var(--color-text); }
.page-subtitle { font-size: var(--text-sm); color: var(--color-text-muted); margin-top: var(--space-1); }
.settings-card { background: var(--color-surface); border-radius: var(--radius-xl); }
.slider-value { min-width: 36px; text-align: right; font-variant-numeric: tabular-nums; font-size: var(--text-sm); color: var(--color-text-muted); }

.test-result {
  display: flex; align-items: center; gap: 8px;
  padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; font-size: var(--text-sm); white-space: pre-wrap;
}
.test-ok { background: rgba(34,197,94,0.1); color: #16a34a; }
.test-fail { background: rgba(239,68,68,0.1); color: #dc2626; }
</style>

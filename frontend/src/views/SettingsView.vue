<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NForm, NFormItem, NInput, NButton, NSelect, NSlider, NInputNumber,
  NCard, NIcon, useMessage, NAlert, NSpace, NDivider, NTooltip,
  NProgress, NTag,
} from 'naive-ui'
import { Settings, Save, Flash, Key, Globe, AlertCircle, CheckmarkCircle, HelpCircle, Server, Download } from '@vicons/ionicons5'
import PageHeader from '@/components/common/PageHeader.vue'
import { getLLMConfig, updateLLMConfig, testLLMConnection, getSandboxNetwork, updateSandboxNetwork, getEmbeddingModelStatus, downloadEmbeddingModel, deleteEmbeddingModel, switchEmbeddingModel, checkEmbeddingDimension, getReindexStatus, type LLMConfig, type SandboxNetworkConfig, type EmbeddingModelStatus, type EmbeddingModelOption, type ReindexStatus } from '@/api/settings'
import PluginManagementSection from '@/components/settings/PluginManagementSection.vue'
import { currentLocale } from '@/i18n/useLocale'

const { t } = useI18n()
const message = useMessage()
const route = useRoute()

const providerOptions = computed(() => [
  { label: 'OpenAI', value: 'openai' },
  { label: t('settings.providerQwen'), value: 'qwen' },
  { label: t('settings.providerOllama'), value: 'ollama' },
  { label: t('settings.providerCustom'), value: 'custom' },
])

const urlDefaults: Record<string, string> = {
  openai: 'https://api.openai.com/v1',
  qwen: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  ollama: 'http://localhost:11434/v1',
  custom: '',
}

const sections = [
  { id: 'llm', label: 'settings.nav.llm' },
  { id: 'embedding-model', label: 'settings.nav.embeddingModel' },
  { id: 'server', label: 'settings.nav.server' },
  { id: 'system-prompt', label: 'settings.nav.systemPrompt' },
  { id: 'plugins', label: 'settings.nav.plugins' },
  { id: 'sandbox-network', label: 'settings.nav.sandboxNetwork' },
]

const networkModeOptions = computed(() => [
  { label: t('settings.networkDeny'), value: 'deny' },
  { label: t('settings.networkAllow'), value: 'allow' },
  { label: t('settings.networkAllowlist'), value: 'allowlist' },
])

const config = ref<LLMConfig>({
  llm_provider: 'openai', llm_model: '', llm_api_key: '',
  llm_base_url: '', llm_temperature: 0.3, llm_max_tokens: 4096,
  agent_max_tokens: 8192,
  llm_context_window: 128000,
  llm_concurrency: 3,
  embedding_model: 'BAAI/bge-small-zh-v1.5',
  embedding_api_key: '',
  llm_system_prompt: '',
  llm_system_prompt_en: '',
  prompt_language: 'system',
  server_host: '0.0.0.0', server_port: 8000,
  cache_ttl_seconds: 3600,
  is_configured: false,
})

const apiKeyInput = ref('')
const saving = ref(false)
const testing = ref(false)
const testResult = ref<{ ok: boolean; text: string } | null>(null)
const activeSection = ref('llm')
const isManualScrolling = ref(false)

// Generated-file retention presets (minutes). "custom" reveals a minutes input.
const keepOptions = computed(() => [
  { label: t('settings.keep1h'), value: 60 },
  { label: t('settings.keep1d'), value: 1440 },
  { label: t('settings.keep1w'), value: 10080 },
  { label: t('settings.keep1m'), value: 43200 },
  { label: t('settings.keepCustom'), value: 'custom' },
])

const sandboxConfig = ref<SandboxNetworkConfig>({
  sandbox_network_mode: 'deny',
  sandbox_allow_domains: '',
  sandbox_allow_methods: '',
  mcp_file_keep_minutes: keepOptions.value[1].value as number,
})

const keepPreset = ref<string | number>(60)
const keepCustomMinutes = ref<number>(60)

function formatKeep(min: number): string {
  if (!min) return t('settings.keepUnset')
  if (min % 43200 === 0) return t('settings.keepMonths', { n: min / 43200 })
  if (min % 10080 === 0) return t('settings.keepWeeks', { n: min / 10080 })
  if (min % 1440 === 0) return t('settings.keepDays', { n: min / 1440 })
  if (min % 60 === 0) return t('settings.keepHours', { n: min / 60 })
  return t('settings.keepMinutes', { n: min })
}

// ── Embedding model (on-demand download) ──
const embeddingStatus = ref<EmbeddingModelStatus>({
  status: 'idle', progress: 0, message: '', error: '', model: '', installed: false,
  configured_model: '', installed_models: [], options: [],
})
const embeddingOptions = ref<EmbeddingModelOption[]>([])
const selectedModel = ref<string>('')
const switching = ref(false)
const embeddingPollTimer = ref<number | null>(null)
// Inline conflict warning shown below the model selector when the dry-run
// dimension check (backend 409) detects an incompatible existing vector store.
const dimensionConflict = ref<string>('')

// Switch / re-index button state, derived from the selected model's install
// status and whether a dimension conflict was detected:
//   - not installed            → disabled, label "switch & re-index"
//   - installed + conflict     → enabled,  label "switch & re-index"
//   - installed + no conflict  → enabled,  label "switch"
function isModelInstalled(m: string) {
  return !!m && !!embeddingStatus.value.installed_models?.includes(m)
}

const switchBtnLabel = computed(() => {
  const installed = isModelInstalled(selectedModel.value)
  const conflict = dimensionConflict.value !== ''
  return installed && !conflict
    ? t('settings.embeddingModelMgmt.switchBtn')
    : t('settings.embeddingModelMgmt.switchReindexBtn')
})

const switchBtnDisabled = computed(() => {
  if (!selectedModel.value || switching.value || reindexing.value) return true
  if (selectedModel.value === embeddingStatus.value.configured_model) return true
  return !isModelInstalled(selectedModel.value)
})

// Hover hint shown when the switch button is disabled, explaining *why*.
// Only returns text for the actionable cases (no model / not installed);
// during loading or when the model is already active there's nothing to hint.
const switchBtnHint = computed(() => {
  if (!switchBtnDisabled.value) return ''
  if (switching.value || reindexing.value) return ''
  if (!selectedModel.value) return t('settings.embeddingModelMgmt.selectFirst')
  if (selectedModel.value === embeddingStatus.value.configured_model) return ''
  if (!isModelInstalled(selectedModel.value)) return t('settings.embeddingModelMgmt.installFirstHint')
  return ''
})

// ── Re-index (after embedding-model switch) ──
const reindexStatus = ref<ReindexStatus>({
  status: 'idle', progress: 0, message: '', error: '', current: 0, total: 0,
})
const reindexing = ref(false)
const reindexPollTimer = ref<number | null>(null)

async function loadReindexStatus() {
  try {
    reindexStatus.value = await getReindexStatus()
    reindexing.value = reindexStatus.value.status === 'running'
  } catch (e: any) {
    // non-fatal
  }
}

function stopReindexPolling() {
  if (reindexPollTimer.value !== null) {
    clearInterval(reindexPollTimer.value)
    reindexPollTimer.value = null
  }
}

function startReindexPolling() {
  if (reindexPollTimer.value !== null) return
  reindexPollTimer.value = window.setInterval(async () => {
    await loadReindexStatus()
    if (reindexStatus.value.status === 'completed' || reindexStatus.value.status === 'failed') {
      stopReindexPolling()
    }
  }, 2000)
}

const embeddingSelectOptions = computed(() =>
  embeddingOptions.value.map((o) => ({ label: o.label, value: o.id })),
)

// ── Selected-model-centric state (the dropdown drives install/download) ──
const selectedInstalled = computed(() =>
  selectedModel.value
    ? embeddingStatus.value.installed_models.includes(selectedModel.value)
    : false,
)
const selectedIsConfigured = computed(() =>
  selectedModel.value === embeddingStatus.value.configured_model,
)
const selectedDownloading = computed(() =>
  embeddingStatus.value.status === 'downloading' &&
  embeddingStatus.value.model === selectedModel.value,
)
const selectedFailed = computed(() =>
  embeddingStatus.value.status === 'failed' &&
  embeddingStatus.value.model === selectedModel.value,
)

async function loadEmbeddingStatus() {
  try {
    const s = await getEmbeddingModelStatus()
    embeddingStatus.value = s
    embeddingOptions.value = s.options || []
    // Only pre-select on first load; never override the user's current choice
    // during subsequent refreshes (e.g. while a download is polling).
    if (s.configured_model && !selectedModel.value) selectedModel.value = s.configured_model
  } catch (e: any) {
    // non-fatal; section just stays in its last known state
  }
}

function stopEmbeddingPolling() {
  if (embeddingPollTimer.value !== null) {
    clearInterval(embeddingPollTimer.value)
    embeddingPollTimer.value = null
  }
}

function startEmbeddingPolling() {
  if (embeddingPollTimer.value !== null) return
  embeddingPollTimer.value = window.setInterval(async () => {
    await loadEmbeddingStatus()
    const s = embeddingStatus.value.status
    if (s === 'completed' || s === 'failed') {
      stopEmbeddingPolling()
      if (s === 'completed') message.success(t('settings.embeddingModelMgmt.installedTip'))
      else message.error(t('settings.embeddingModelMgmt.statusFailed') + '：' + embeddingStatus.value.error)
    }
  }, 2000)
}

async function handleDownloadEmbedding() {
  try {
    const res = await downloadEmbeddingModel(selectedModel.value || undefined)
    if (res.started) {
      await loadEmbeddingStatus()
      startEmbeddingPolling()
      message.info(t('settings.embeddingModelMgmt.downloading'))
    } else if (res.reason === 'already_downloading') {
      await loadEmbeddingStatus()
      startEmbeddingPolling()
      message.warning(t('settings.embeddingModelMgmt.alreadyDownloading'))
    }
  } catch (e: any) {
    message.error(e.message || t('settings.saveFailed'))
  }
}

async function handleDeleteEmbedding() {
  try {
    await deleteEmbeddingModel(selectedModel.value || undefined)
    await loadEmbeddingStatus()
    message.success(t('settings.embeddingModelMgmt.deleted'))
  } catch (e: any) {
    message.error(e.message || t('settings.saveFailed'))
  }
}

// When the dropdown changes, only *query* whether the new model's dimension is
// compatible with the existing vector store. Nothing is mutated here — if the
// backend returns 409 we show an inline warning below the selector telling the
// user that switching will require clearing and rebuilding the indexes. The
// actual clear+rebuild happens when they click "Re-index All".
async function onSelectModelChange(target: string) {
  if (!target || target === embeddingStatus.value.configured_model) {
    dimensionConflict.value = ''
    return
  }
  dimensionConflict.value = ''
  try {
    await checkEmbeddingDimension(target)
  } catch (e: any) {
    const resp = e?.response
    if (resp?.status === 409) {
      const d = resp.data?.detail ?? {}
      dimensionConflict.value = t('settings.embeddingModelMgmt.dimensionConflictTip', {
        model: target,
        existing: d.existing_dim,
        neu: d.new_dim,
        count: d.vector_count,
      })
    }
  }
}

async function onSwitched(res: { model: string; installed: boolean; cleared_vectors: boolean; reindex_started: boolean }) {
  config.value.embedding_model = res.model
  await loadEmbeddingStatus()
  if (!res.installed) {
    message.warning(t('settings.embeddingModelMgmt.switchedNotInstalled'))
  } else if (res.cleared_vectors) {
    message.warning(t('settings.embeddingModelMgmt.switchedCleared'))
  } else {
    message.success(t('settings.embeddingModelMgmt.switched'))
  }
  if (res.cleared_vectors) {
    await loadReindexStatus()
    if (res.reindex_started) startReindexPolling()
    else message.info(t('settings.embeddingModelMgmt.reindexPendingDownload'))
  }
}

// "Re-index All" now means: clear existing vector indexes, switch to the
// selected model, and rebuild — i.e. switch(force=True). If the target model is
// still downloading, the re-index is queued and runs when the download finishes.
async function handleReindex() {
  const target = selectedModel.value
  if (!target) return
  switching.value = true
  try {
    const res = await switchEmbeddingModel(target, true)
    await onSwitched(res)
  } catch (e: any) {
    message.error(e?.response?.data?.detail ?? e?.message ?? t('settings.saveFailed'))
  } finally {
    switching.value = false
  }
}

let observer: IntersectionObserver | null = null
let scrollTimer: number | null = null

onMounted(async () => {
  try {
    config.value = await getLLMConfig()
    if (!config.value.llm_context_window) config.value.llm_context_window = 128000
  } catch (e: any) {
    message.error(e.message || t('settings.msg.loadConfigFailed'))
  }

  try {
    sandboxConfig.value = await getSandboxNetwork()
    const v = sandboxConfig.value.mcp_file_keep_minutes ?? 60
    const preset = keepOptions.value.find((o) => o.value !== 'custom' && o.value === v)
    keepPreset.value = preset ? v : 'custom'
    keepCustomMinutes.value = v
  } catch (e: any) {
    message.error(e.message || t('settings.msg.loadSandboxFailed'))
  }

  await loadEmbeddingStatus()
  await loadReindexStatus()
  if (reindexing.value) startReindexPolling()

  if (route.hash) {
    const id = route.hash.slice(1)
    setTimeout(() => scrollTo(id), 100)
  }

  observer = new IntersectionObserver(
    (entries) => {
      if (isManualScrolling.value) return
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          activeSection.value = entry.target.id
        }
      })
    },
    { rootMargin: '-80px 0px -60% 0px', threshold: 0 },
  )
  sections.forEach((s) => {
    const el = document.getElementById(s.id)
    if (el) observer!.observe(el)
  })
})

onUnmounted(() => {
  observer?.disconnect()
  stopEmbeddingPolling()
  stopReindexPolling()
})

function clearTest() { testResult.value = null }

function onProviderChange(val: string) {
  // Auto-fill default base_url when switching providers
  const defaultUrl = urlDefaults[val] ?? ''
  if (defaultUrl && (!config.value.llm_base_url || Object.values(urlDefaults).includes(config.value.llm_base_url))) {
    config.value.llm_base_url = defaultUrl
  }
}

// Effective prompt language: 'system' follows the global UI locale (zh-CN -> zh, en-US -> en).
const effectivePromptLang = computed<'zh' | 'en'>(() => {
  const v = config.value.prompt_language
  if (v === 'system') return currentLocale.value === 'en-US' ? 'en' : 'zh'
  return v === 'en' ? 'en' : 'zh'
})

// System-prompt textarea binds to the field matching the effective prompt language:
// zh <-> llm_system_prompt, en <-> llm_system_prompt_en.
// Both fields are persisted independently, so switching never overwrites the other.
const systemPromptModel = computed<string>({
  get() {
    return effectivePromptLang.value === 'en'
      ? config.value.llm_system_prompt_en
      : config.value.llm_system_prompt
  },
  set(v: string) {
    if (effectivePromptLang.value === 'en') {
      config.value.llm_system_prompt_en = v
    } else {
      config.value.llm_system_prompt = v
    }
  },
})

// Prompt-language selector options: follow system / Chinese / English.
const promptLangOptions = computed(() => [
  { label: t('settings.promptLang.system'), value: 'system' },
  { label: t('settings.promptLang.zh'), value: 'zh' },
  { label: t('settings.promptLang.en'), value: 'en' },
])

// Helper text shown next to the selector.
const promptLangHint = computed(() => {
  const v = config.value.prompt_language
  if (v === 'system') {
    const sys = currentLocale.value === 'en-US' ? t('settings.promptLang.en') : t('settings.promptLang.zh')
    return `${t('settings.promptLang.system')}（${sys}）`
  }
  return v === 'en' ? t('settings.promptLang.en') : t('settings.promptLang.zh')
})

function scrollTo(id: string) {
  const el = document.getElementById(id)
  if (!el) return
  isManualScrolling.value = true
  if (scrollTimer !== null) {
    clearTimeout(scrollTimer)
  }
  el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  activeSection.value = id
  history.replaceState(null, '', `#${id}`)
  scrollTimer = window.setTimeout(() => {
    isManualScrolling.value = false
  }, 800)
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
      agent_max_tokens: config.value.agent_max_tokens,
      llm_context_window: config.value.llm_context_window,
      llm_concurrency: config.value.llm_concurrency,
      embedding_model: config.value.embedding_model,
      llm_system_prompt: config.value.llm_system_prompt,
      llm_system_prompt_en: config.value.llm_system_prompt_en,
      prompt_language: config.value.prompt_language === 'system'
        ? (currentLocale.value === 'en-US' ? 'en' : 'zh')
        : config.value.prompt_language,
      server_host: config.value.server_host,
      server_port: config.value.server_port,
      cache_ttl_seconds: config.value.cache_ttl_seconds,
    }
    if (apiKeyInput.value.trim()) {
      payload.llm_api_key = apiKeyInput.value.trim()
    }
    const res = await updateLLMConfig(payload)
    config.value = res.config
    apiKeyInput.value = ''
    testResult.value = null
    message.success(t('settings.msg.configSaved'))

    // 同步保存沙盒网络设置
    const keepMins = keepPreset.value === 'custom' ? keepCustomMinutes.value : Number(keepPreset.value)
    const sres = await updateSandboxNetwork({
      sandbox_network_mode: sandboxConfig.value.sandbox_network_mode,
      sandbox_allow_domains: sandboxConfig.value.sandbox_allow_domains,
      sandbox_allow_methods: sandboxConfig.value.sandbox_allow_methods,
      mcp_file_keep_minutes: keepMins,
    })
    sandboxConfig.value = sres.config
    if (sres.mcp_pushed) {
      message.success(t('settings.msg.sandboxSavedHot'))
    } else {
      message.warning(t('settings.msg.sandboxSavedRestart'))
    }
  } catch (e: any) {
    message.error(e.message || t('settings.msg.saveFailed'))
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
      ? { ok: true, text: t('settings.test.success', { model: res.model, reply: res.reply }) }
      : { ok: false, text: t('settings.test.failed', { error: res.error }) }
  } catch (e: any) {
    testResult.value = { ok: false, text: t('settings.test.error', { message: e.message }) }
  } finally {
    testing.value = false
  }
}

</script>

<template>
  <div class="settings-layout">
    <div class="settings-sticky-top">
      <PageHeader :title="t('settings.title')" :icon="Settings" :subtitle="t('settings.subtitle')">
        <template #actions>
          <NButton
            size="small"
            type="primary"
            :loading="saving"
            :disabled="!apiKeyInput.trim() && !config.is_configured"
            @click="handleSave"
          >
            <template #icon><NIcon><Save /></NIcon></template>
            {{ t('settings.saveConfig') }}
          </NButton>
        </template>
      </PageHeader>

      <!-- 子导航 -->
      <nav class="settings-subnav">
      <a
        v-for="s in sections"
        :key="s.id"
        :class="['subnav-link', { active: activeSection === s.id }]"
        @click.prevent="scrollTo(s.id)"
      >
        {{ t(s.label) }}
      </a>
    </nav>
  </div>

  <div class="settings-page">
    <!-- 未配置警告 -->
    <NAlert
      v-if="!config.is_configured"
      type="warning"
      :title="t('settings.alertTitle')"
      :bordered="false"
      style="margin-bottom: 16px"
    >
      <template #icon><NIcon :component="AlertCircle" /></template>
      {{ t('settings.alertDesc') }}
    </NAlert>

    <NCard :bordered="false" class="settings-card">
      <NForm label-placement="left" label-width="160">

        <!-- LLM -->
        <section id="llm">
          <!-- Provider -->
          <NFormItem :label="t('settings.providerLabel')">
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
              :placeholder="config.is_configured ? (config.api_key_source === 'env' ? t('settings.apiKey.currentEnv', { key: config.llm_api_key }) : t('settings.apiKey.current', { key: config.llm_api_key })) : t('settings.apiKey.placeholder')"
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
          <NFormItem :label="t('settings.modelName')">
            <NInput v-model:value="config.llm_model" placeholder="gpt-4o-mini" @input="clearTest">
              <template #prefix><NIcon :component="Settings" /></template>
            </NInput>
          </NFormItem>

          <!-- Temperature -->
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                Temperature
                <NTooltip trigger="hover" :width="280">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  <span v-html="t('settings.tip.temperature')" />
                </NTooltip>
              </span>
            </template>
            <NSpace align="center">
              <NSlider v-model:value="config.llm_temperature" :min="0" :max="2" :step="0.05" style="width: 200px" @update:value="clearTest" />
              <span class="slider-value">{{ config.llm_temperature.toFixed(2) }}</span>
            </NSpace>
          </NFormItem>

          <!-- Max Tokens -->
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                Max Tokens
                <NTooltip trigger="hover" :width="280">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  <span v-html="t('settings.tip.maxTokens')" />
                </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.llm_max_tokens" :min="128" :max="131072" :step="256" @update:value="clearTest" />
          </NFormItem>

          <!-- Agent Max Tokens -->
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                Agent Max Tokens
                <NTooltip trigger="hover" :width="320">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  <span v-html="t('settings.tip.agentMaxTokens')" />
                </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.agent_max_tokens" :min="128" :max="131072" :step="256" @update:value="clearTest" />
          </NFormItem>

          <!-- LLM Concurrency -->
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                {{ t('settings.maxConcurrency') }}
                <NTooltip trigger="hover" :width="300">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  <span v-html="t('settings.tip.maxConcurrency')" />
                </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.llm_concurrency" :min="1" :max="50" :step="1" @update:value="clearTest" />
          </NFormItem>

          <!-- Context Window -->
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                {{ t('settings.contextWindow') }}
                <NTooltip trigger="hover" :width="320">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  <span v-html="t('settings.tip.contextWindow')" />
                </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.llm_context_window" :min="1" :max="10000000" :step="1000" @update:value="clearTest" />
          </NFormItem>

          <!-- Cache TTL -->
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                {{ t('settings.cacheTtl') }}
                <NTooltip trigger="hover" :width="300">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  <span v-html="t('settings.tip.cacheTtl')" />
                </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.cache_ttl_seconds" :min="0" :max="864000" :step="300" @update:value="clearTest" />
            <span class="muted" style="margin-left:8px;font-size:12px">
              {{ config.cache_ttl_seconds === 0 ? t('common.disabled') : t('settings.cacheSecondsApprox', { seconds: config.cache_ttl_seconds, minutes: Math.round(config.cache_ttl_seconds / 60) }) }}
            </span>
          </NFormItem>

          <!-- Test Connection -->
          <NFormItem :show-feedback="false">
            <NSpace align="center">
              <NButton
                type="info"
                :loading="testing"
                :disabled="!apiKeyInput.trim() && !config.is_configured"
                @click="handleTest"
              >
                <template #icon><NIcon><Flash /></NIcon></template>
                {{ t('settings.testConnection') }}
              </NButton>
              <div v-if="testResult" :class="['test-result', testResult.ok ? 'test-ok' : 'test-fail']">
                <NIcon :component="testResult.ok ? CheckmarkCircle : AlertCircle" size="16" />
                <span>{{ testResult.text }}</span>
              </div>
            </NSpace>
          </NFormItem>

        </section>

        <NDivider />

        <!-- Embedding Model (on-demand install) -->
        <section id="embedding-model">
          <h3 class="section-title">{{ t('settings.embeddingModelMgmt.title') }}</h3>
          <p class="muted" style="margin: 0 0 16px;font-size: 13px" v-html="t('settings.embeddingModelMgmt.desc')" />
          <NFormItem :label="t('settings.embeddingModelMgmt.currentLabel')">
            <span class="muted" style="font-size: 14px; font-weight: 500">
              {{ embeddingStatus.configured_model || config.embedding_model }}
            </span>
          </NFormItem>
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                {{ t('settings.embeddingModelMgmt.select') }}
                <NTooltip trigger="hover" :width="280">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  {{ t('settings.embeddingModelTip') }}
                </NTooltip>
              </span>
            </template>
            <div style="display: flex; align-items: center; gap: 12px; width: 100%">
              <NSelect
                v-model:value="selectedModel"
                :options="embeddingSelectOptions"
                :placeholder="t('settings.embeddingModelMgmt.selectPlaceholder')"
                :disabled="selectedDownloading"
                style="flex: 1"
                @update:value="onSelectModelChange"
              />
              <NTooltip :disabled="!switchBtnHint" placement="top">
                <template #trigger>
                  <span style="display: inline-flex">
                    <NButton
                      type="primary"
                      :loading="switching || reindexing"
                      :disabled="switchBtnDisabled"
                      @click="handleReindex"
                    >
                      <template #icon><NIcon><Flash /></NIcon></template>
                      {{ switchBtnLabel }}
                    </NButton>
                  </span>
                </template>
                {{ switchBtnHint }}
              </NTooltip>
            </div>
          </NFormItem>

          <NFormItem v-if="selectedModel" :label="t('settings.embeddingModelMgmt.status')">
            <NSpace align="center" :size="12">
              <NTag v-if="selectedInstalled && !selectedDownloading" type="success" :bordered="false" round>
                {{ t('settings.embeddingModelMgmt.statusInstalled') }}
              </NTag>
              <NTag v-else-if="selectedDownloading" type="warning" :bordered="false" round>
                {{ t('settings.embeddingModelMgmt.statusDownloading') }}
              </NTag>
              <NTag v-else-if="selectedFailed" type="error" :bordered="false" round>
                {{ t('settings.embeddingModelMgmt.statusFailed') }}
              </NTag>
              <NTag v-else type="default" :bordered="false" round>
                {{ t('settings.embeddingModelMgmt.statusNotInstalled') }}
              </NTag>

              <span class="muted" style="font-size: 12px">
                <template v-if="selectedDownloading">
                  {{ embeddingStatus.message }}
                </template>
                <template v-else-if="selectedInstalled">
                  {{ t('settings.embeddingModelMgmt.installedTip') }}
                </template>
                <template v-else>
                  {{ t('settings.embeddingModelMgmt.notInstalledTip') }}
                </template>
              </span>
              
              <NButton
                v-if="!selectedInstalled && !selectedDownloading"
                type="primary"
                :disabled="!selectedModel"
                @click="handleDownloadEmbedding"
              >
                <template #icon><NIcon><Download /></NIcon></template>
                {{ t('settings.embeddingModelMgmt.download') }}
              </NButton>

              <NButton
                v-if="selectedInstalled"
                type="error"
                secondary
                :disabled="selectedIsConfigured"
                @click="handleDeleteEmbedding"
              >
                {{ t('settings.embeddingModelMgmt.delete') }}
              </NButton>

            </NSpace>

            <div v-if="selectedDownloading" style="margin-top: 10px">
              <span class="muted" style="font-size: 12px; margin-right: 8px">{{ t('settings.embeddingModelMgmt.downloadProgress') }}</span>
              <NProgress
                type="line"
                :percentage="embeddingStatus.progress"
                :show-indicator="true"
                :processing="true"
                style="width: 200px"
              />
            </div>
          </NFormItem>

          <NFormItem
            v-if="embeddingStatus.installed_models && embeddingStatus.installed_models.length"
            :label="t('settings.embeddingModelMgmt.installedList')"
          >
            <NSpace :size="8">
              <NTag
                v-for="m in embeddingStatus.installed_models"
                :key="m"
                :type="m === embeddingStatus.configured_model ? 'success' : 'default'"
                :bordered="false"
                round
              >
                {{ m }}
              </NTag>
            </NSpace>
          </NFormItem>

          <NFormItem v-if="selectedFailed" :show-feedback="false">
            <NAlert type="error" :bordered="false" :title="t('settings.embeddingModelMgmt.statusFailed')">
              {{ embeddingStatus.error }}
            </NAlert>
          </NFormItem>

          <NFormItem v-if="reindexStatus.status !== 'idle'" :show-feedback="false">
            <NAlert
              :type="reindexStatus.status === 'failed' ? 'error' : (reindexStatus.status === 'completed' ? 'success' : 'warning')"
              :bordered="false"
              :title="t('settings.embeddingModelMgmt.reindexTitle')"
            >
              <div style="font-size: 13px">
                {{ reindexStatus.message || reindexStatus.error }}
              </div>
              <NProgress
                v-if="reindexStatus.status === 'running'"
                type="line"
                :percentage="reindexStatus.progress"
                :show-indicator="true"
                :processing="true"
                style="margin-top: 8px; max-width: 360px"
              />
              <div v-if="reindexStatus.status === 'running'" class="muted" style="font-size: 12px; margin-top: 4px">
                {{ reindexStatus.current }} / {{ reindexStatus.total }}
              </div>
            </NAlert>
          </NFormItem>

          <NAlert
            v-if="dimensionConflict"
            type="warning"
            :bordered="false"
            :title="t('settings.embeddingModelMgmt.conflictTitle')"
            style="margin-top: 8px"
          >
            {{ dimensionConflict }}
          </NAlert>
          
        </section>

        <NDivider />

        <!-- Server -->
        <section id="server">
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                {{ t('settings.listenHost') }}
                <NTooltip trigger="hover" :width="260">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  <span v-html="t('settings.listenHostTip')" />
                </NTooltip>
              </span>
            </template>
            <NInput v-model:value="config.server_host" placeholder="0.0.0.0" @input="clearTest">
              <template #prefix><NIcon :component="Server" /></template>
            </NInput>
          </NFormItem>

          <NFormItem>
            <template #label>
              <span class="label-with-help">
                {{ t('settings.listenPort') }}
                <NTooltip trigger="hover" :width="260">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  <span v-html="t('settings.listenPortTip')" />
                </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.server_port" :min="1" :max="65535" :step="1" @update:value="clearTest" />
          </NFormItem>
        </section>

        <NDivider />

        <!-- System Prompt -->
        <section id="system-prompt">
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                {{ t('settings.systemPrompt') }}
                <NTooltip trigger="hover" :width="300">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  <span v-html="t('settings.systemPromptTip')" />
                </NTooltip>
              </span>
            </template>
            <NInput
              v-model:value="systemPromptModel"
              type="textarea"
              :rows="10"
              :placeholder="t('settings.systemPrompt')"
              @input="clearTest"
            />
          </NFormItem>

          <NFormItem>
            <template #label>
              <span class="label-with-help">
                {{ t('settings.agentPromptLang') }}
                <NTooltip trigger="hover" :width="320">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  <span v-html="t('settings.agentPromptLangTip')" />
                </NTooltip>
              </span>
            </template>
            <NSpace align="center">
              <NSelect
                v-model:value="config.prompt_language"
                :options="promptLangOptions"
                size="small"
                style="width: 160px"
              />
              <span class="muted" style="font-size: 13px">
                {{ promptLangHint }}
              </span>
            </NSpace>
          </NFormItem>
        </section>

      </NForm>

      <NDivider />

      <section id="plugins">
        <PluginManagementSection />
      </section>
    </NCard>

    <NCard :bordered="false" class="settings-card" style="margin-top: 16px">
      <section id="sandbox-network">
        <h3 class="section-title">{{ t('settings.sandboxTitle') }}</h3>
        <p class="muted" style="margin: 0 0 16px;font-size: 13px" v-html="t('settings.sandboxDesc')" />
        <NForm label-placement="left" label-width="140">
          <NFormItem :label="t('settings.networkMode')">
            <NSelect v-model:value="sandboxConfig.sandbox_network_mode" :options="networkModeOptions" />
          </NFormItem>
          <NFormItem v-if="sandboxConfig.sandbox_network_mode === 'allowlist'" :label="t('settings.allowDomains')">
            <NInput
              v-model:value="sandboxConfig.sandbox_allow_domains"
              type="textarea"
              :rows="3"
              placeholder="api.github.com, raw.githubusercontent.com"
            />
          </NFormItem>
          <NFormItem>
            <span class="muted" style="font-size: 12px">
              {{ t('settings.currentMode', { mode: sandboxConfig.sandbox_network_mode }) }}
              <template v-if="sandboxConfig.sandbox_network_mode === 'allowlist'">
                （{{ sandboxConfig.sandbox_allow_domains || t('settings.noDomainsConfigured') }}）
              </template>
            </span>
          </NFormItem>

          <NDivider />

          <NFormItem :label="t('settings.fileRetention')">
            <NSpace vertical :size="8" style="width: 100%">
              <NSelect v-model:value="keepPreset" :options="keepOptions" style="max-width: 240px" />
              <NInputNumber
                v-if="keepPreset === 'custom'"
                v-model:value="keepCustomMinutes"
                :min="1" :max="525600" :step="60"
                :placeholder="t('settings.customMinutesPlaceholder')"
                style="max-width: 240px"
              >
                <template #suffix>{{ t('settings.minutes') }}</template>
              </NInputNumber>
              <span class="muted" style="font-size: 12px">
                {{ t('settings.currentRetention', { keep: formatKeep(sandboxConfig.mcp_file_keep_minutes) }) }}
              </span>
            </NSpace>
          </NFormItem>

        </NForm>
      </section>
    </NCard>
  </div>
</div>
</template>

<style scoped>
.settings-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
}
.settings-sticky-top {
  flex-shrink: 0;
  max-width: 1100px;
  width: 100%;
  margin: 0 auto;
  background: var(--color-bg);
}
.settings-page {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  max-width: 1100px;
  width: 100%;
  margin: 0 auto;
}

.settings-subnav {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-2) var(--space-3);
  margin-bottom: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow-x: auto;
}
.subnav-link {
  flex-shrink: 0;
  padding: 6px 12px;
  border-radius: var(--radius);
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  white-space: nowrap;
}
.subnav-link:hover {
  background: var(--color-primary-soft);
  color: var(--color-primary);
}
.subnav-link.active {
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-weight: 600;
}

.settings-card { background: var(--color-surface); border-radius: var(--radius-xl); }
.settings-card section { scroll-margin-top: 0; }
.slider-value { min-width: 36px; text-align: right; font-variant-numeric: tabular-nums; font-size: var(--text-sm); color: var(--color-text-muted); }

.label-with-help { display: inline-flex; align-items: center; gap: 4px; }
.help-icon { color: var(--color-text-muted); cursor: help; transition: color 0.15s; }
.help-icon:hover { color: var(--color-primary); }

.test-result {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 12px; border-radius: 6px; font-size: var(--text-sm); white-space: nowrap; line-height: 1;
}
.test-ok { background: rgba(34,197,94,0.1); color: #16a34a; }
.test-fail { background: rgba(239,68,68,0.1); color: #dc2626; }

</style>

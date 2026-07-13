<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NForm, NFormItem, NInput, NButton, NSelect, NSlider, NInputNumber,
  NCard, NIcon, useMessage, NAlert, NSpace, NDivider, NTooltip,
} from 'naive-ui'
import { Settings, Save, Flash, Key, Globe, AlertCircle, CheckmarkCircle, HelpCircle, HardwareChip, Server } from '@vicons/ionicons5'
import PageHeader from '@/components/common/PageHeader.vue'
import { getLLMConfig, updateLLMConfig, testLLMConnection, getSandboxNetwork, updateSandboxNetwork, type LLMConfig, type SandboxNetworkConfig } from '@/api/settings'
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
  { id: 'server', label: 'settings.nav.server' },
  { id: 'system-prompt', label: 'settings.nav.systemPrompt' },
  { id: 'sandbox-network', label: 'settings.nav.sandboxNetwork' },
  { id: 'plugins', label: 'settings.nav.plugins' },
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

const keepPreset = ref<string>('60')
const keepCustomMinutes = ref<number>(60)

function formatKeep(min: number): string {
  if (!min) return t('settings.keepUnset')
  if (min % 43200 === 0) return t('settings.keepMonths', { n: min / 43200 })
  if (min % 10080 === 0) return t('settings.keepWeeks', { n: min / 10080 })
  if (min % 1440 === 0) return t('settings.keepDays', { n: min / 1440 })
  if (min % 60 === 0) return t('settings.keepHours', { n: min / 60 })
  return t('settings.keepMinutes', { n: min })
}
const savingSandbox = ref(false)

let observer: IntersectionObserver | null = null
let scrollTimer: number | null = null

onMounted(async () => {
  try {
    config.value = await getLLMConfig()
  } catch (e: any) {
    message.error(e.message || t('settings.msg.loadConfigFailed'))
  }

  try {
    sandboxConfig.value = await getSandboxNetwork()
    const v = sandboxConfig.value.mcp_file_keep_minutes ?? 60
    const preset = keepOptions.value.find((o) => o.value !== 'custom' && o.value === v)
    keepPreset.value = preset ? String(v) : 'custom'
    keepCustomMinutes.value = v
  } catch (e: any) {
    message.error(e.message || t('settings.msg.loadSandboxFailed'))
  }

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

async function handleSaveSandbox() {
  savingSandbox.value = true
  try {
    const keepMins = keepPreset.value === 'custom' ? keepCustomMinutes.value : Number(keepPreset.value)
    const res = await updateSandboxNetwork({
      sandbox_network_mode: sandboxConfig.value.sandbox_network_mode,
      sandbox_allow_domains: sandboxConfig.value.sandbox_allow_domains,
      sandbox_allow_methods: sandboxConfig.value.sandbox_allow_methods,
      mcp_file_keep_minutes: keepMins,
    })
    sandboxConfig.value = res.config
    if (res.mcp_pushed) {
      message.success(t('settings.msg.sandboxSavedHot'))
    } else {
      message.warning(t('settings.msg.sandboxSavedRestart'))
    }
  } catch (e: any) {
    message.error(e.message || t('settings.msg.saveFailed'))
  } finally {
    savingSandbox.value = false
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
          <!-- Embedding Model -->
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                {{ t('settings.embeddingModel') }}
                <NTooltip trigger="hover" :width="260">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  {{ t('settings.embeddingModelTip') }}
                </NTooltip>
              </span>
            </template>
            <NInput v-model:value="config.embedding_model" placeholder="BAAI/bge-small-zh-v1.5" @input="clearTest" disabled>
              <template #prefix><NIcon :component="HardwareChip" /></template>
            </NInput>
          </NFormItem>
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

          <NDivider />

          <NFormItem :label="t('settings.fileRetention')">
            <NSpace vertical :size="8" style="width: 100%">
              <NSelect v-model:value="keepPreset" :options="keepOptions" style="max-width: 240px" />
              <NInputNumber
                v-if="keepPreset === 'custom'"
                v-model:value="keepCustomMinutes"
                :min="1" :max="525600" :step="60"
                :placeholder="t('settings.customMinutesPlaceholder')"
              >
                <template #suffix>{{ t('settings.minutes') }}</template>
              </NInputNumber>
              <span class="muted" style="font-size: 12px">
                {{ t('settings.currentRetention', { keep: formatKeep(sandboxConfig.mcp_file_keep_minutes) }) }}
              </span>
            </NSpace>
          </NFormItem>

          <NFormItem>
            <NSpace align="center">
              <NButton type="primary" :loading="savingSandbox" @click="handleSaveSandbox">{{ t('settings.saveSandbox') }}</NButton>
              <span class="muted" style="font-size: 12px">
                {{ t('settings.currentMode', { mode: sandboxConfig.sandbox_network_mode }) }}
                <template v-if="sandboxConfig.sandbox_network_mode === 'allowlist'">
                  （{{ sandboxConfig.sandbox_allow_domains || t('settings.noDomainsConfigured') }}）
                </template>
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

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import {
  NForm, NFormItem, NInput, NButton, NSelect, NSlider, NInputNumber,
  NCard, NIcon, useMessage, NAlert, NSpace, NDivider, NTooltip, NSwitch,
} from 'naive-ui'
import { Settings, Save, Flash, Key, Globe, AlertCircle, CheckmarkCircle, HelpCircle, HardwareChip, Server } from '@vicons/ionicons5'
import PageHeader from '@/components/common/PageHeader.vue'
import { getLLMConfig, updateLLMConfig, testLLMConnection, getSandboxNetwork, updateSandboxNetwork, type LLMConfig, type SandboxNetworkConfig } from '@/api/settings'
import PluginManagementSection from '@/components/settings/PluginManagementSection.vue'

const message = useMessage()
const route = useRoute()

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

const sections = [
  { id: 'llm', label: 'LLM' },
  { id: 'server', label: '服务器' },
  { id: 'system-prompt', label: '系统提示词' },
  { id: 'sandbox-network', label: '沙盒网络' },
  { id: 'plugins', label: '插件管理' },
]

const networkModeOptions = [
  { label: 'deny（默认，禁止所有网络访问）', value: 'deny' },
  { label: 'allow（全放开，仅调试用）', value: 'allow' },
  { label: 'allowlist（仅允许白名单域名）', value: 'allowlist' },
]

const config = ref<LLMConfig>({
  llm_provider: 'openai', llm_model: '', llm_api_key: '',
  llm_base_url: '', llm_temperature: 0.3, llm_max_tokens: 4096,
  agent_max_tokens: 8192,
  llm_concurrency: 3,
  embedding_model: 'BAAI/bge-small-zh-v1.5',
  embedding_api_key: '',
  llm_system_prompt: '',
  llm_system_prompt_en: '',
  prompt_language: 'zh',
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

const sandboxConfig = ref<SandboxNetworkConfig>({
  sandbox_network_mode: 'deny',
  sandbox_allow_domains: '',
  sandbox_allow_methods: '',
})
const savingSandbox = ref(false)

let observer: IntersectionObserver | null = null
let scrollTimer: number | null = null

onMounted(async () => {
  try {
    config.value = await getLLMConfig()
  } catch (e: any) {
    message.error(e.message || '加载配置失败')
  }

  try {
    sandboxConfig.value = await getSandboxNetwork()
  } catch (e: any) {
    message.error(e.message || '加载沙盒网络配置失败')
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

// System-prompt textarea binds to the field matching the current prompt language:
// toggle off (zh) <-> llm_system_prompt, toggle on (en) <-> llm_system_prompt_en.
// Both fields are persisted independently, so switching never overwrites the other.
const systemPromptModel = computed<string>({
  get() {
    return config.value.prompt_language === 'en'
      ? config.value.llm_system_prompt_en
      : config.value.llm_system_prompt
  },
  set(v: string) {
    if (config.value.prompt_language === 'en') {
      config.value.llm_system_prompt_en = v
    } else {
      config.value.llm_system_prompt = v
    }
  },
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
      prompt_language: config.value.prompt_language,
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

async function handleSaveSandbox() {
  savingSandbox.value = true
  try {
    const res = await updateSandboxNetwork({
      sandbox_network_mode: sandboxConfig.value.sandbox_network_mode,
      sandbox_allow_domains: sandboxConfig.value.sandbox_allow_domains,
      sandbox_allow_methods: sandboxConfig.value.sandbox_allow_methods,
    })
    sandboxConfig.value = res.config
    if (res.mcp_pushed) {
      message.success('沙盒网络策略已保存并热加载，立即生效')
    } else {
      message.warning('已保存（MCP 热加载未成功，重启 MCP 容器后生效）')
    }
  } catch (e: any) {
    message.error(e.message || '保存失败')
  } finally {
    savingSandbox.value = false
  }
}
</script>

<template>
  <div class="settings-layout">
    <div class="settings-sticky-top">
      <PageHeader title="系统设置" :icon="Settings" subtitle="LLM · 服务器 · 检索调试 · 插件管理 · 仅超级管理员可访问">
        <template #actions>
          <NButton
            size="small"
            type="primary"
            :loading="saving"
            :disabled="!apiKeyInput.trim() && !config.is_configured"
            @click="handleSave"
          >
            <template #icon><NIcon><Save /></NIcon></template>
            保存配置
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
        {{ s.label }}
      </a>
    </nav>
  </div>

  <div class="settings-page">
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
      <NForm label-placement="left" label-width="160">

        <!-- LLM -->
        <section id="llm">
          <!-- Embedding Model -->
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                Embedding 模型
                <NTooltip trigger="hover" :width="260">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  默认使用本地Embedding模型。
                </NTooltip>
              </span>
            </template>
            <NInput v-model:value="config.embedding_model" placeholder="BAAI/bge-small-zh-v1.5" @input="clearTest" disabled>
              <template #prefix><NIcon :component="HardwareChip" /></template>
            </NInput>
          </NFormItem>
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
              :placeholder="config.is_configured ? (config.api_key_source === 'env' ? `当前: ${config.llm_api_key}（来自 .env 文件，留空不修改）` : `当前: ${config.llm_api_key}（留空不修改）`) : '请输入 API Key（首次录入）'"
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
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                Temperature
                <NTooltip trigger="hover" :width="280">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  控制 LLM 输出的随机性与创造性。范围 0~2。<br/>
                  <b>0</b> = 最确定，每次回答一致；<b>2</b> = 最随机。<br/>
                  RAG 场景建议 <b>0.1~0.5</b>，让 LLM 严格遵循检索文档，减少自由发挥。
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
                  LLM 单次输出的最大 token 数（≈ 中文字数 × 1.5~2）。<br/>
                  设太小回答会被截断，设太大浪费额度。<br/>
                  RAG 场景 <b>1024~4096</b> 通常足够，该值同时限制最终回答长度。
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
                    Agent 在「工具决策」环节单次输出的最大 token 数，<b>独立于上面的 Max Tokens</b>。<br/>
                    工具调用的参数（代码 / 文档 / 查询结果）可能很大，设太小会导致参数 JSON 被截断、工具调用失败。<br/>
                    默认 <b>8192</b>，比普通回答更宽松；除非模型上下文极小，一般无需调小。
                  </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.agent_max_tokens" :min="128" :max="131072" :step="256" @update:value="clearTest" />
          </NFormItem>

          <!-- LLM Concurrency -->
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                最大并发数
                <NTooltip trigger="hover" :width="300">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  同时向 LLM API 发送请求的会话数上限，超过后进入排队。<br/>
                  建议按服务商账户等级设置，OpenAI Tier 1 通常设为 3~5。<br/>
                  <b>修改后立即生效</b>，不会强行中断已在处理的请求。
                </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.llm_concurrency" :min="1" :max="50" :step="1" @update:value="clearTest" />
          </NFormItem>

          <!-- Cache TTL -->
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                缓存有效期
                <NTooltip trigger="hover" :width="300">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  相同问题的回答缓存有效时间（秒）。<br/>
                  默认 3600 秒（60 分钟）。设为 0 可完全禁用缓存。<br/>
                  <b>修改后立即生效</b>，不影响已缓存的条目（按各自创建时间判定过期）。
                </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.cache_ttl_seconds" :min="0" :max="864000" :step="300" @update:value="clearTest" />
            <span class="muted" style="margin-left:8px;font-size:12px">
              {{ config.cache_ttl_seconds === 0 ? '已禁用' : (config.cache_ttl_seconds + ' 秒 ≈ ' + Math.round(config.cache_ttl_seconds / 60) + ' 分钟') }}
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
                测试连接
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
                监听地址
                <NTooltip trigger="hover" :width="260">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  服务器绑定的 IP 地址。<br/>
                  <b>0.0.0.0</b> = 接受所有网络接口的连接。<br/>
                  <b>修改后需重启服务生效</b>。
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
                监听端口
                <NTooltip trigger="hover" :width="260">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  服务器监听的 TCP 端口，范围 1~65535。<br/>
                  <b>修改后需重启服务生效</b>。
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
                LLM 系统提示词
                <NTooltip trigger="hover" :width="300">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  用于 RAG 和 Agent 默认回复的系统提示词。<br/>
                  修改后立即生效，无需重启。
                </NTooltip>
              </span>
            </template>
            <NInput
              v-model:value="systemPromptModel"
              type="textarea"
              :rows="10"
              placeholder="请输入系统提示词..."
              @input="clearTest"
            />
          </NFormItem>

          <NFormItem>
            <template #label>
              <span class="label-with-help">
                Agent 提示词语言
                <NTooltip trigger="hover" :width="320">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                    控制 Agent Graph 内部提示词（意图路由 + 工具强制 JSON）使用的语言。<br/>
                    默认 <b>中文</b>；切到 <b>English</b> 可对比英文主导模型（GPT / Claude / DeepSeek 等）的指令遵循率。<br/>
                    修改后立即生效，无需重启，便于 A/B 对比。
                  </NTooltip>
              </span>
            </template>
            <NSpace align="center">
              <NSwitch
                v-model:value="config.prompt_language"
                checked-value="en"
                unchecked-value="zh"
              />
              <span class="muted" style="font-size: 13px">
                {{ config.prompt_language === 'en' ? 'English（英文）' : '中文（默认）' }}
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
        <h3 class="section-title">沙盒网络策略（REPL 代码执行环境）</h3>
        <p class="muted" style="margin: 0 0 16px;font-size: 13px">
          控制 LLM 生成的代码在沙盒中能否访问外部网络。默认 <b>deny</b>（完全禁止，最安全）。
          选择 <b>allowlist</b> 后可填写允许访问的域名（逗号或换行分隔），Python 代码仅能访问白名单域名，
          且直连 IP 会被拦截（防 DNS 重绑）。修改后通过 MCP 服务热加载，<b>立即生效，无需重启</b>。
        </p>
        <NForm label-placement="left" label-width="140">
          <NFormItem label="网络策略模式">
            <NSelect v-model:value="sandboxConfig.sandbox_network_mode" :options="networkModeOptions" />
          </NFormItem>
          <NFormItem v-if="sandboxConfig.sandbox_network_mode === 'allowlist'" label="允许域名">
            <NInput
              v-model:value="sandboxConfig.sandbox_allow_domains"
              type="textarea"
              :rows="3"
              placeholder="api.github.com, raw.githubusercontent.com"
            />
          </NFormItem>
          <NFormItem>
            <NSpace align="center">
              <NButton type="primary" :loading="savingSandbox" @click="handleSaveSandbox">保存沙盒网络策略</NButton>
              <span class="muted" style="font-size: 12px">
                当前：{{ sandboxConfig.sandbox_network_mode }}
                <template v-if="sandboxConfig.sandbox_network_mode === 'allowlist'">
                  （{{ sandboxConfig.sandbox_allow_domains || '未配置域名' }}）
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

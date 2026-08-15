<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { backendErrorMessage } from '@/utils/backendError'
import { budgetWarningText as sharedBudgetWarningText, type BudgetWarning } from '@/utils/budgetWarning'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NForm, NFormItem, NInput, NButton, NSelect, NInputNumber,
  NCard, NIcon, useMessage, useDialog, NAlert, NSpace, NDivider, NTooltip,
  NProgress, NTag, NSwitch,
} from 'naive-ui'
import { Settings, Save, Flash, Key, Globe, AlertCircle, CheckmarkCircle, HelpCircle, Download, Refresh, Copy, Pause, Play, CloseCircle } from '@vicons/ionicons5'
import PageHeader from '@/components/common/PageHeader.vue'
import { getLLMConfig, updateLLMConfig, testLLMConnection, getSandboxNetwork, updateSandboxNetwork, getReplAuth, regenerateReplAuth, getEmbeddingModelStatus, downloadEmbeddingModel, pauseEmbeddingDownload, resumeEmbeddingDownload, cancelEmbeddingDownload, deleteEmbeddingModel, switchEmbeddingModel, checkEmbeddingDimension, switchToNone, getReindexStatus, startReindex, getHttpsConfig, updateHttpsConfig, getJwtAuth, regenerateJwtAuth, type LLMConfig, type SandboxNetworkConfig, type ReplAuthConfig, type EmbeddingModelStatus, type EmbeddingModelOption, type ReindexStatus, type HTTPSConfig, type JwtAuthConfig } from '@/api/settings'
import PluginManagementSection from '@/components/settings/PluginManagementSection.vue'
import { currentLocale } from '@/i18n/useLocale'
import { useAuthStore } from '@/stores/auth'

const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()
const route = useRoute()
const auth = useAuthStore()

const pluginStats = ref({ enabled: 0, total: 0, loading: true })
const pluginSectionRef = ref()
function onPluginStats(s: { enabled: number; total: number; loading: boolean }) {
  pluginStats.value = s
}
function onPluginRefresh() {
  pluginSectionRef.value?.refresh()
}

const sections = [
  { id: 'llm', label: 'settings.nav.llm' },
  { id: 'embedding-model', label: 'settings.nav.embeddingModel' },
  { id: 'system-prompt', label: 'settings.nav.systemPrompt' },
  { id: 'https', label: 'settings.nav.https' },
  { id: 'sandbox-network', label: 'settings.nav.sandboxNetwork' },
  { id: 'repl-auth', label: 'settings.nav.replAuth' },
  { id: 'jwt-auth', label: 'settings.nav.jwtAuth' },
  { id: 'plugins', label: 'settings.nav.plugins' },
]

const networkModeOptions = computed(() => [
  { label: t('settings.networkDeny'), value: 'deny' },
  { label: t('settings.networkAllow'), value: 'allow' },
  { label: t('settings.networkAllowlist'), value: 'allowlist' },
])

const config = ref<LLMConfig>({
  llm_model: '', llm_api_key: '',
  llm_base_url: '', llm_temperature: 0.3,   llm_max_tokens: 4096,
  llm_context_window: 192000,
  llm_concurrency: 3,
  embedding_model: '',
  embedding_api_key: '',
  llm_system_prompt: '',
  llm_system_prompt_en: '',
  prompt_language: 'en',
  cache_ttl_seconds: 3600,
  agent_round_quota: 20,
  skill_switch_quota: 10,
  summary_archive_high_pct: 40,
  is_configured: false,
})

const apiKeyInput = ref('')
const testing = ref(false)
const testResult = ref<{ ok: boolean; text: string } | null>(null)

// Local mirror of backend config_manager.is_valid_llm_config: all three fields
// present AND free of obvious errors (api_key/model non-empty, base_url a valid
// http(s) URL). Used to disable the Test button without a round-trip.
function isValidLlmConfig() {
  const key = (apiKeyInput.value || config.value.llm_api_key || '').trim()
  const model = (config.value.llm_model || '').trim()
  const url = (config.value.llm_base_url || '').trim()
  if (!key || !model) return false
  try {
    const u = new URL(url)
    return u.protocol === 'http:' || u.protocol === 'https:'
  } catch {
    return false
  }
}
const activeSection = ref('llm')
const isManualScrolling = ref(false)

const sandboxConfig = ref<SandboxNetworkConfig>({
  sandbox_network_mode: 'deny',
  sandbox_allow_domains: '',
  sandbox_allow_methods: '',
})

// ── REPL MCP identity secret ──
const replAuthSecret = ref<string>('')
const replAuthGenerating = ref(false)
const replAuthPushed = ref<boolean | null>(null)

// ── JWT signing secret ──
const jwtAuthSecret = ref<string>('')
const jwtAuthGenerating = ref(false)

// ── HTTPS / TLS (nginx reverse proxy, prod only) ──
const httpsEnabled = ref(false)
const httpsCert = ref('')
const httpsKey = ref('')
const httpsSaving = ref(false)
const httpsMeta = ref<{ subject: string; expires: string } | null>(null)
const httpsDirty = ref(false)

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
  // Disabling vector search ("None") → the action is Switch & Clear All.
  if (selectedModel.value === '') {
    return t('settings.embeddingModelMgmt.switchClearBtn')
  }
  const conflict = dimensionConflict.value !== ''
  return installed && !conflict
    ? t('settings.embeddingModelMgmt.switchBtn')
    : t('settings.embeddingModelMgmt.switchReindexBtn')
})

const switchBtnDisabled = computed(() => {
  // "None" (disable vector search): actionable only when vector search is
  // currently enabled, i.e. the configured model is not already empty.
  if (selectedModel.value === '') {
    if (switching.value || reindexing.value) return true
    return embeddingStatus.value.configured_model === ''
  }
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
  // Model not installed → always hint to install first, even when it happens to be
  // the currently configured (but not yet installed) model.
  if (!isModelInstalled(selectedModel.value)) return t('settings.embeddingModelMgmt.installFirstHint')
  // Installed and already the active model → nothing to do, no hint.
  if (selectedModel.value === embeddingStatus.value.configured_model) return ''
  return ''
})

// ── Re-index (after embedding-model switch) ──
const reindexStatus = ref<ReindexStatus>({
  status: 'idle', phase: '', params: {}, progress: 0, error: '', current: 0, total: 0,
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

// Backend emits a machine-readable phase code + structured params; the
// frontend resolves it to a localized string via the i18n reindexPhases map.
// Raw exception detail (if any) stays in `error` for tooltip/log only.
function reindexPhaseLabel(phase: string, params: Record<string, any> = {}): string {
  if (!phase) return ''
  return (t(`settings.embeddingModelMgmt.reindexPhases.${phase}`, params) || '') as string
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
    const st = reindexStatus.value.status
    if (st === 'completed' || st === 'failed') {
      stopReindexPolling()
      if (st === 'completed') {
        // The re-index (incl. the force/clear switch path) has rebuilt every
        // vector against the now-configured model, so any prior dimension
        // conflict is resolved — drop the stale inline warning for good.
        dimensionConflict.value = ''
        message.success(t('settings.embeddingModelMgmt.reindexDone'))
      } else {
        message.error(reindexStatus.value.error || t('settings.embeddingModelMgmt.reindexFailed'))
      }
    }
  }, 2000)
}

// vue-i18n uses "." as a path separator, so the model ids (which contain "."
// and "/", e.g. "BAAI/bge-small-zh-v1.5") cannot be used directly as i18n key
// paths. Map each id to a special-character-free key under modelLabels.
const MODEL_LABEL_KEY: Record<string, string> = {
  'BAAI/bge-small-zh-v1.5': 'bgeSmallZh',
  'BAAI/bge-base-zh-v1.5': 'bgeBaseZh',
  'BAAI/bge-large-zh-v1.5': 'bgeLargeZh',
  'BAAI/bge-small-en-v1.5': 'bgeSmallEn',
  'BAAI/bge-base-en-v1.5': 'bgeBaseEn',
  'BAAI/bge-large-en-v1.5': 'bgeLargeEn',
  '': 'none',
}

const embeddingSelectOptions = computed(() => {
  // Touch currentLocale so this recomputes when the UI language changes.
  void currentLocale.value
  return embeddingOptions.value.map((o) => ({
    label: t(`settings.embeddingModelMgmt.modelLabels.${MODEL_LABEL_KEY[o.id] ?? ''}`, o.label),
    value: o.id,
  }))
})

// Localized name of the currently configured model, matching the dropdown labels.
// Falls back to the raw id when the id isn't in the i18n map (unknown model).
const currentModelLabel = computed(() => {
  const id = embeddingStatus.value.configured_model || config.value.embedding_model || ''
  return t(`settings.embeddingModelMgmt.modelLabels.${MODEL_LABEL_KEY[id] ?? ''}`, id || 'None')
})

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

const selectedPaused = computed(() =>
  embeddingStatus.value.status === 'paused' &&
  embeddingStatus.value.model === selectedModel.value,
)

const selectedCancelled = computed(() =>
  embeddingStatus.value.status === 'cancelled' &&
  embeddingStatus.value.model === selectedModel.value,
)

// "active" = a download is in progress (downloading or paused) for this model.
// Used to decide whether to show pause/resume/cancel and suppress the separate
// download button + model selector.
const selectedActiveDownload = computed(() =>
  selectedDownloading.value || selectedPaused.value,
)

async function loadEmbeddingStatus() {
  try {
    const s = await getEmbeddingModelStatus()
    embeddingStatus.value = s
    embeddingOptions.value = s.options || []
    // Only pre-select on first load; never override the user's current choice
    // during subsequent refreshes (e.g. while a download is polling).
    if (!selectedModel.value) {
      // Pre-select the currently configured model (empty string = "None" /
      // vector search disabled is a valid state, not a language default).
      selectedModel.value = s.configured_model || ""
    }
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
    if (s === 'completed' || s === 'failed' || s === 'cancelled') {
      stopEmbeddingPolling()
      if (s === 'completed') message.success(t('settings.embeddingModelMgmt.installedTip'))
      else if (s === 'failed') message.error(t('settings.embeddingModelMgmt.statusFailed') + ': ' + embeddingStatus.value.error)
      // cancelled: handled by the cancel action's own toast
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
    message.error(backendErrorMessage(e.message) || t('settings.saveFailed'))
  }
}

function handleDeleteEmbedding() {
  const model = selectedModel.value
  // Deleting removes the local model files (and its install record) — require a
  // second confirmation, just like the switch / rebuild flows.
  dialog.warning({
    title: t('settings.embeddingModelMgmt.deleteConfirmTitle'),
    content: t('settings.embeddingModelMgmt.deleteConfirmContent', { model }),
    positiveText: t('settings.embeddingModelMgmt.deleteConfirmOk'),
    negativeText: t('common.cancel'),
    onPositiveClick: () => doDeleteEmbedding(),
  })
}

async function doDeleteEmbedding() {
  try {
    await deleteEmbeddingModel(selectedModel.value || undefined)
    await loadEmbeddingStatus()
    message.success(t('settings.embeddingModelMgmt.deleted'))
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('settings.saveFailed'))
  }
}

// ── Download pause / resume / cancel ──
async function handlePauseDownload() {
  try {
    const res = await pauseEmbeddingDownload()
    if (res.paused) {
      await loadEmbeddingStatus()
      message.info(t('settings.embeddingModelMgmt.paused'))
    }
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('settings.saveFailed'))
  }
}

async function handleResumeDownload() {
  try {
    const res = await resumeEmbeddingDownload()
    if (res.resumed) {
      await loadEmbeddingStatus()
      startEmbeddingPolling()
      message.info(t('settings.embeddingModelMgmt.resumed'))
    }
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('settings.saveFailed'))
  }
}

async function handleCancelDownload() {
  try {
    const res = await cancelEmbeddingDownload()
    if (res.cancelled) {
      await loadEmbeddingStatus()
      stopEmbeddingPolling()
      message.info(t('settings.embeddingModelMgmt.cancelled'))
    }
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('settings.saveFailed'))
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

// "Re-index All" = clear existing vector indexes, switch to the selected model,
// and rebuild — i.e. switch(force=True). This only runs for an already-installed
// model (the switch button is disabled otherwise); to use a new model the user
// must download & install it first, then switch.
async function doReindex(target: string) {
  switching.value = true
  // Seed the deletion-phase panel the instant the confirm modal closes. The
  // backend switch response is now fast (the model weights load lazily inside the
  // re-index worker, not in the request), but we seed optimistically so the
  // The "deleting old vectors…" line shows with zero wait even on a slow network round-trip.
  reindexStatus.value = {
    status: 'running',
    phase: 'deleting',
    params: {},
    progress: 0,
    current: 0,
    total: 0,
    error: '',
  }
  reindexing.value = true
  try {
    const res = await switchEmbeddingModel(target, true)
    config.value.embedding_model = res.model
    await loadEmbeddingStatus()
    if (res.cleared_vectors && res.reindex_started) {
      // Backend already reports the "deleting" phase (set synchronously in
      // reindex_service.start()); the poller keeps the panel in sync from here.
      startReindexPolling()
    } else {
      // No vectors to clear (or reindex didn't start) — nothing to show.
      reindexing.value = false
    }
  } catch (e: any) {
    message.error(e?.response?.data?.detail ?? e?.message ?? t('settings.saveFailed'))
    stopReindexPolling()
    reindexStatus.value = { status: 'idle', phase: '', params: {}, progress: 0, error: '', current: 0, total: 0 }
    reindexing.value = false
  } finally {
    switching.value = false
  }
}

async function handleReindex() {
  const target = selectedModel.value
  if (!target) return
  // "None" selected → disable vector search AND wipe all vectors. This is an
  // explicit, destructive action, so always require a second confirmation.
  if (target === '') {
    dialog.warning({
      title: t('settings.embeddingModelMgmt.switchNoneTitle'),
      content: t('settings.embeddingModelMgmt.switchNoneConfirm'),
      positiveText: t('settings.embeddingModelMgmt.switchNoneOk'),
      negativeText: t('common.cancel'),
      onPositiveClick: () => { doSwitchNone() },
    })
    return
  }
  // When the button reads "Switch & Re-index" (an incompatible-dimension switch),
  // the action will wipe ALL vector indexes. Pop a second confirmation that
  // re-states the dimension-conflict warning before proceeding.
  if (dimensionConflict.value) {
    dialog.warning({
      title: t('settings.embeddingModelMgmt.conflictTitle'),
      content: dimensionConflict.value,
      positiveText: t('settings.embeddingModelMgmt.conflictOk'),
      negativeText: t('common.cancel'),
      onPositiveClick: () => { doReindex(target) },
    })
    return
  }
  await doReindex(target)
}

// Switch to "None" (disable vector search) and clear all vectors in one action.
async function doSwitchNone() {
  switching.value = true
  reindexStatus.value = {
    status: 'running',
    phase: 'deleting',
    params: {},
    progress: 0,
    current: 0,
    total: 0,
    error: '',
  }
  reindexing.value = true
  try {
    const res = await switchToNone()
    config.value.embedding_model = res.model
    await loadEmbeddingStatus()
    message.success(t('settings.embeddingModelMgmt.switchNoneDone'))
  } catch (e: any) {
    message.error(e?.response?.data?.detail ?? e?.message ?? t('settings.saveFailed'))
  } finally {
    switching.value = false
    reindexing.value = false
    reindexStatus.value = { status: 'idle', phase: '', params: {}, progress: 0, error: '', current: 0, total: 0 }
  }
}

// "Re-index Now" = re-embed ALL documents (including chunked ones that were
// saved before the model existed) against the currently installed & active
// model, without switching models. This drives the dedicated POST
// /documents/reindex endpoint, which was previously never wired to the UI —
// so a user who installed the already-configured model had no way to vectorize
// their chunked documents (the only other re-index trigger is the switch flow,
// which is disabled when selecting the active model).
async function handleRebuildIndex() {
  if (reindexing.value) return
  const model = embeddingStatus.value.configured_model || config.value.embedding_model
  // Heavy, all-corpus operation (re-embeds every document + rebuilds every
  // vector index) — require a second confirmation, just like the switch flow.
  dialog.warning({
    title: t('settings.embeddingModelMgmt.rebuildConfirmTitle'),
    content: t('settings.embeddingModelMgmt.rebuildConfirmContent', { model }),
    positiveText: t('settings.embeddingModelMgmt.rebuildConfirmOk'),
    negativeText: t('common.cancel'),
    onPositiveClick: () => { doRebuildIndex() },
  })
}

async function doRebuildIndex() {
  if (reindexing.value) return
  try {
    const res = await startReindex()
    if (res.started) {
      await loadReindexStatus()
      startReindexPolling()
      message.info(t('settings.embeddingModelMgmt.reindexStarted'))
    } else if (res.reason === 'already_running') {
      await loadReindexStatus()
      startReindexPolling()
      message.warning(t('settings.embeddingModelMgmt.reindexRunning'))
    }
  } catch (e: any) {
    message.error(e?.response?.data?.detail ?? e?.message ?? t('settings.saveFailed'))
  }
}

let observer: IntersectionObserver | null = null
let scrollTimer: number | null = null

onMounted(async () => {
  try {
    config.value = await getLLMConfig()
    // Old configs may store 'system' (follow-system), which is no longer an option.
    if (config.value.prompt_language === 'system' || !config.value.prompt_language) {
      config.value.prompt_language = 'en'
    }
    if (!config.value.llm_context_window) config.value.llm_context_window = 128000
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('settings.msg.loadConfigFailed'))
  }

  try {
    sandboxConfig.value = await getSandboxNetwork()
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('settings.msg.loadSandboxFailed'))
  }

  try {
    const ra = await getReplAuth()
    replAuthSecret.value = ra.repl_auth_secret || ''
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('settings.msg.loadReplAuthFailed'))
  }

  try {
    const ja = await getJwtAuth()
    jwtAuthSecret.value = ja.jwt_secret || ''
  } catch (e: any) {
    message.error(e.message || t('settings.msg.loadJwtAuthFailed'))
  }

  try {
    const hs = await getHttpsConfig()
    httpsEnabled.value = hs.https_enabled
    httpsMeta.value = hs.cert_meta
  } catch (e: any) {
    message.error(backendErrorMessage(e.message) || t('settings.msg.loadHttpsFailed'))
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
  // Flush any pending (debounced) auto-save so the last edit is not lost on navigation.
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
    void doSave()
  }
})

function clearTest() { testResult.value = null }

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
  { label: t('settings.promptLang.zh'), value: 'zh' },
  { label: t('settings.promptLang.en'), value: 'en' },
])

// Helper text shown next to the selector.
const promptLangHint = computed(() => {
  const v = config.value.prompt_language
  // 'system' (follow-system) is no longer an option; treat anything other than
  // 'zh' as English, the new default.
  return v === 'zh' ? t('settings.promptLang.zh') : t('settings.promptLang.en')
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
  history.replaceState(history.state ?? {}, '', `#${id}`)
  scrollTimer = window.setTimeout(() => {
    isManualScrolling.value = false
  }, 800)
}

// ── Auto-save (debounced): persists on field blur / Enter / select / slider release ──
type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'
const saveState = ref<{ status: SaveStatus; label?: string; msg?: string }>({ status: 'idle' })
let saveTimer: ReturnType<typeof setTimeout> | null = null
// Field names changed within the current debounce window; shown in the inline
// status (e.g. "API Key saved"). A Set dedupes and collapses multiple edits.
const pendingLabels = new Set<string>()

// Non-blocking warnings returned by the backend config sanity check (window
// headroom, oversized system prompt). Shown as a yellow bar after a save;
// cleared when the next save reports no warnings. Structured as { code, params }
// per project convention: the backend emits a bare code and the frontend
// localizes it -- shared with the KB / profile / pin save paths via
// utils/budgetWarning.
const budgetWarnings = ref<BudgetWarning[]>([])

function budgetWarningText(w: BudgetWarning): string {
  return sharedBudgetWarningText(w, t)
}

function scheduleSave(label?: unknown) {
  // Only accept string labels; control events may pass the event/value object.
  if (typeof label === 'string' && label) pendingLabels.add(label)
  saveState.value = { status: 'saving', label: [...pendingLabels].join(', ') }
  if (saveTimer) clearTimeout(saveTimer)
  // Debounce so rapid commits (e.g. tabbing through fields) collapse into one request.
  saveTimer = setTimeout(() => {
    saveTimer = null
    void doSave()
  }, 300)
}

// Localize the auto-save failure reason. The API client interceptor already
// folds the backend `detail` / axios message into `e.message`, so we map by
// HTTP status and network category to stable i18n strings, and append the raw
// server detail only when it carries business-specific info (e.g. 4xx).
function saveErrorReason(e: any): string {
  const status: number | undefined = e?.response?.status
  const detail: string = e?.message || ''
  const isAxiosGeneric = /^Request failed with status code \d+/i.test(detail) || /^timeout/i.test(detail)
  const businessDetail = detail && !isAxiosGeneric
  if (status) {
    const map: Record<number, string> = {
      400: t('settings.msg.saveFailedBadRequest'),
      401: t('settings.msg.saveFailedUnauthorized'),
      403: t('settings.msg.saveFailedForbidden'),
      404: t('settings.msg.saveFailedNotFound'),
      409: t('settings.msg.saveFailedConflict'),
      422: t('settings.msg.saveFailedValidation'),
      429: t('settings.msg.saveFailedRateLimit'),
      500: t('settings.msg.saveFailedServer'),
      502: t('settings.msg.saveFailedUnavailable'),
      503: t('settings.msg.saveFailedUnavailable'),
      504: t('settings.msg.saveFailedUnavailable'),
    }
    const base = map[status] || t('settings.msg.saveFailedServer')
    // Append the raw server detail only for client-side (4xx) errors where it
    // usually explains the rejection; 5xx details are typically unhelpful traces.
    if (businessDetail && status < 500) return `${base} (${detail})`
    return base
  }
  if (/timeout/i.test(detail)) return t('settings.msg.saveFailedTimeout')
  if (/network error/i.test(detail)) return t('settings.msg.saveFailedNetwork')
  if (businessDetail) return `${t('settings.msg.saveFailedUnknown')} (${detail})`
  return t('settings.msg.saveFailedUnknown')
}

async function doSave() {
  const changed = [...pendingLabels]
  pendingLabels.clear()
  const payload: Record<string, any> = {
    llm_model: config.value.llm_model,
    llm_base_url: config.value.llm_base_url,
    llm_temperature: config.value.llm_temperature,
    llm_max_tokens: config.value.llm_max_tokens,
    llm_context_window: config.value.llm_context_window,
    llm_concurrency: config.value.llm_concurrency,
    embedding_model: config.value.embedding_model,
    llm_system_prompt: config.value.llm_system_prompt,
    llm_system_prompt_en: config.value.llm_system_prompt_en,
    prompt_language: config.value.prompt_language,
    cache_ttl_seconds: config.value.cache_ttl_seconds,
    agent_round_quota: config.value.agent_round_quota,
    skill_switch_quota: config.value.skill_switch_quota,
    summary_archive_high_pct: config.value.summary_archive_high_pct,
  }
  if (apiKeyInput.value.trim()) {
    payload.llm_api_key = apiKeyInput.value.trim()
  }
  try {
    const res = await updateLLMConfig(payload)
    config.value = res.config
    // Surface non-blocking config sanity warnings (e.g. context-window headroom)
    // as a yellow bar. Empty when the backend reports no issue.
    budgetWarnings.value = res.config?.warnings || []
    if (config.value.prompt_language === 'system' || !config.value.prompt_language) {
      config.value.prompt_language = 'en'
    }
    apiKeyInput.value = ''
    testResult.value = null
    const sres = await updateSandboxNetwork({
      sandbox_network_mode: sandboxConfig.value.sandbox_network_mode,
      sandbox_allow_domains: sandboxConfig.value.sandbox_allow_domains,
      sandbox_allow_methods: sandboxConfig.value.sandbox_allow_methods,
    })
    sandboxConfig.value = sres.config
    // Inline status instead of a toast (toasts disappear too fast to read).
    // Preserve the "sandbox needs restart" hint inline when not hot-pushed.
    saveState.value = {
      status: 'saved',
      label: changed.join(', '),
      msg: sres.mcp_pushed ? undefined : t('settings.msg.sandboxSavedRestart'),
    }
  } catch (e: any) {
    saveState.value = { status: 'error', label: changed.join(', '), msg: saveErrorReason(e) }
  }
}

async function handleReplAuthGenerate() {
  replAuthGenerating.value = true
  try {
    const res = await regenerateReplAuth()
    replAuthSecret.value = res.repl_auth_secret
    replAuthPushed.value = res.mcp_pushed
    if (res.mcp_pushed) {
      message.success(t('settings.msg.replAuthGeneratedHot'))
    } else {
      message.warning(t('settings.msg.replAuthSavedRestart'))
    }
  } catch (e: any) {
    message.error(saveErrorReason(e))
  } finally {
    replAuthGenerating.value = false
  }
}

async function handleReplAuthCopy() {
  try {
    await navigator.clipboard.writeText(replAuthSecret.value)
    message.success(t('settings.replAuthCopied'))
  } catch {
    message.warning(t('settings.replAuthCopyFailed'))
  }
}

async function handleJwtAuthGenerate() {
  jwtAuthGenerating.value = true
  try {
    const res = await regenerateJwtAuth()
    jwtAuthSecret.value = res.jwt_secret
    // Rotation invalidates all issued tokens — warn the admin to re-login.
    message.warning(t('settings.msg.jwtAuthGeneratedHot'))
  } catch (e: any) {
    message.error(saveErrorReason(e))
  } finally {
    jwtAuthGenerating.value = false
  }
}

async function handleJwtAuthCopy() {
  try {
    await navigator.clipboard.writeText(jwtAuthSecret.value)
    message.success(t('settings.jwtAuthCopied'))
  } catch {
    message.warning(t('settings.jwtAuthCopyFailed'))
  }
}

async function handleHttpsSave() {
  httpsSaving.value = true
  try {
    const res = await updateHttpsConfig({
      https_enabled: httpsEnabled.value,
      https_cert: httpsEnabled.value ? httpsCert.value : '',
      https_key: httpsEnabled.value ? httpsKey.value : '',
    })
    httpsEnabled.value = res.https_enabled
    httpsMeta.value = res.cert_meta
    httpsDirty.value = false
    // Secrets are not echoed back; clear the inputs after a successful save.
    httpsCert.value = ''
    httpsKey.value = ''
    message.success(t('settings.httpsSaved'))
  } catch (e: any) {
    message.error(saveErrorReason(e))
  } finally {
    httpsSaving.value = false
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
          <span class="save-status" :class="saveState.status">
            <template v-if="saveState.status === 'saving'"><template v-if="saveState.label">{{ saveState.label }}</template> {{ t('settings.msg.saving') }}</template>
            <template v-else-if="saveState.status === 'saved'">
              <NIcon :component="CheckmarkCircle" size="14" /> <template v-if="saveState.label">{{ saveState.label }}</template> {{ t('settings.msg.saved') }}<template v-if="saveState.msg"> ({{ saveState.msg }})</template>
            </template>
            <template v-else-if="saveState.status === 'error'">
              <NIcon :component="AlertCircle" size="14" /> <template v-if="saveState.label">{{ saveState.label }}</template> {{ t('settings.msg.saveFailedPrefix') }}{{ saveState.msg }}
            </template>
          </span>
        </template>
      </PageHeader>

      <!-- Sub-navigation -->
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
    <!-- Unconfigured warning -->
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

    <!-- Context-window headroom warning (non-blocking, backend sanity check) -->
    <NAlert
      v-if="budgetWarnings.length"
      type="warning"
      :title="t('settings.contextWindowWarningTitle')"
      :bordered="false"
      style="margin-bottom: 16px"
    >
      <template #icon><NIcon :component="AlertCircle" /></template>
      <ul style="margin: 0;padding-left: 18px">
        <li v-for="(w, i) in budgetWarnings" :key="i" style="margin-bottom: 4px">{{ budgetWarningText(w) }}</li>
      </ul>
    </NAlert>

    <NCard :bordered="false" class="settings-card">
      <NForm label-placement="left" label-width="160">

        <!-- LLM -->
        <section id="llm">
          <div style="display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 16px">
            <div>
              <h3 class="section-title" style="margin: 0 0 4px">{{ t('settings.llmTitle') }}</h3>
              <p class="muted" style="margin: 0;font-size: 13px">{{ t('settings.llmDesc') }}</p>
            </div>
            <NSpace align="center">
              <NButton
                type="info"
                :loading="testing"
                :disabled="!isValidLlmConfig()"
                @click="handleTest"
              >
                <template #icon><NIcon><Flash /></NIcon></template>
                {{ t('settings.testConnection') }}
              </NButton>
              <div v-if="testResult" :class="['test-result', testResult.ok ? 'test-ok' : 'test-fail']" style="max-width: 320px; white-space: normal; word-break: break-word">
                <NIcon :component="testResult.ok ? CheckmarkCircle : AlertCircle" size="16" />
                <span>{{ testResult.text }}</span>
              </div>
            </NSpace>
          </div>
          <!-- API Key -->
          <NFormItem label="API Key" :required="!config.is_configured">
            <NInput
              v-model:value="apiKeyInput"
              type="password"
              show-password-on="click"
              :placeholder="config.is_configured ? (config.api_key_source === 'env' ? t('settings.apiKey.currentEnv', { key: config.llm_api_key }) : t('settings.apiKey.current', { key: config.llm_api_key })) : t('settings.apiKey.placeholder')"
              maxlength="512"
              @input="clearTest"
              @change="scheduleSave('API Key')"
            >
              <template #prefix><NIcon :component="Key" /></template>
            </NInput>
          </NFormItem>

          <!-- Base URL -->
          <NFormItem label="Base URL">
            <NInput v-model:value="config.llm_base_url" placeholder="https://api.openai.com/v1" @input="clearTest" @change="scheduleSave('Base URL')">
              <template #prefix><NIcon :component="Globe" /></template>
            </NInput>
          </NFormItem>

          <!-- Model -->
          <NFormItem :label="t('settings.modelName')">
            <NInput v-model:value="config.llm_model" placeholder="gpt-4o-mini" @input="clearTest" @change="scheduleSave(t('settings.modelName'))">
              <template #prefix><NIcon :component="Settings" /></template>
            </NInput>
          </NFormItem>

          <!-- Temperature -->
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                {{ t('settings.temperature') }}
                <NTooltip trigger="hover" :width="280">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  <span>{{ t('settings.tip.temperature') }}</span>
                </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.llm_temperature" :min="0" :max="1" :step="0.05" @update:value="clearTest(); scheduleSave(t('settings.temperature'))" />
          </NFormItem>

          <!-- Max Tokens -->
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                {{ t('settings.maxTokens') }}
                <NTooltip trigger="hover" :width="280">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  <span>{{ t('settings.tip.maxTokens') }}</span>
                </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.llm_max_tokens" :min="128" :max="131072" :step="256" @update:value="clearTest(); scheduleSave(t('settings.maxTokens'))" />
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
                  <span>{{ t('settings.tip.contextWindow') }}</span>
                </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.llm_context_window" :min="1" :max="10000000" :step="1000" @update:value="clearTest(); scheduleSave(t('settings.contextWindow'))" />
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
                  <span>{{ t('settings.tip.maxConcurrency') }}</span>
                </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.llm_concurrency" :min="1" :max="50" :step="1" @update:value="clearTest(); scheduleSave(t('settings.maxConcurrency'))" />
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
                  <span>{{ t('settings.tip.cacheTtl') }}</span>
                </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.cache_ttl_seconds" :min="0" :max="864000" :step="300" @update:value="clearTest(); scheduleSave(t('settings.cacheTtl'))" />
            <span class="muted" style="margin-left:8px;font-size:12px">
              {{ config.cache_ttl_seconds === 0 ? t('common.disabled') : t('settings.cacheSecondsApprox', { seconds: config.cache_ttl_seconds, minutes: Math.round(config.cache_ttl_seconds / 60) }) }}
            </span>
          </NFormItem>

          <!-- Agent round quota (applies to all chats + cron) -->
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                {{ t('settings.agentRoundQuota') }}
                <NTooltip trigger="hover" :width="320">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  <span>{{ t('settings.tip.agentRoundQuota') }}</span>
                </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.agent_round_quota" :min="0" :max="200" :step="1" @update:value="clearTest(); scheduleSave(t('settings.agentRoundQuota'))" />
            <span class="muted" style="margin-left:8px;font-size:12px">
              {{ config.agent_round_quota === 0 ? t('settings.agentRoundQuotaUnlimited') : t('settings.agentRoundQuotaHint', { n: config.agent_round_quota }) }}
            </span>
          </NFormItem>

          <!-- Skill-switch cap (applies to all chats + cron) -->
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                {{ t('settings.skillSwitchQuota') }}
                <NTooltip trigger="hover" :width="320">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  <span>{{ t('settings.tip.skillSwitchQuota') }}</span>
                </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.skill_switch_quota" :min="0" :max="200" :step="1" @update:value="clearTest(); scheduleSave(t('settings.skillSwitchQuota'))" />
            <span class="muted" style="margin-left:8px;font-size:12px">
              {{ config.skill_switch_quota === 0 ? t('settings.skillSwitchQuotaUnlimited') : t('settings.skillSwitchQuotaHint', { n: config.skill_switch_quota }) }}
            </span>
          </NFormItem>

          <!-- Memory archive thresholds: crossing the L0 share triggers folding older segments into the archive -->
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                {{ t('settings.archiveHigh') }}
                <NTooltip trigger="hover" :width="340">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  <span>{{ t('settings.tip.archiveHigh') }}</span>
                </NTooltip>
              </span>
            </template>
            <NInputNumber v-model:value="config.summary_archive_high_pct" :min="1" :max="100" :step="1" @update:value="clearTest(); scheduleSave(t('settings.archiveHigh'))" />
            <span class="muted" style="margin-left:8px;font-size:12px">%</span>
          </NFormItem>

        </section>
      </NForm>
    </NCard>

    <NCard :bordered="false" class="settings-card" style="margin-top: 16px">
      <NForm label-placement="left" label-width="160">
        <!-- Embedding Model (on-demand install) -->
        <section id="embedding-model">
          <h3 class="section-title">{{ t('settings.embeddingModelMgmt.title') }}</h3>
          <p class="muted" style="margin: 0 0 16px;font-size: 13px">{{ t('settings.embeddingModelMgmt.desc') }}</p>
          <NFormItem :label="t('settings.embeddingModelMgmt.currentLabel')">
            <span class="muted" style="font-size: 14px; font-weight: 500">
              {{ currentModelLabel }}
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
                :disabled="selectedActiveDownload"
                style="flex: 1"
                @update:value="onSelectModelChange"
              />
              <NButton
                v-if="selectedInstalled && selectedIsConfigured"
                type="primary"
                :loading="reindexing"
                :disabled="reindexing"
                @click="handleRebuildIndex"
              >
                <template #icon><NIcon><Refresh /></NIcon></template>
                {{ t('settings.embeddingModelMgmt.rebuildIndexBtn') }}
              </NButton>
              <NTooltip v-else :disabled="!switchBtnHint" placement="top">
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
            <!-- Active download row: status tag | progress | pause/cancel | info -->
            <div v-if="selectedActiveDownload" style="display: flex; align-items: center; gap: 12px; width: 100%; flex-wrap: wrap">
              <NTag v-if="selectedDownloading" type="warning" :bordered="false" round>
                {{ t('settings.embeddingModelMgmt.statusDownloading') }}
              </NTag>
              <NTag v-else-if="selectedPaused" type="warning" :bordered="false" round>
                {{ t('settings.embeddingModelMgmt.statusPaused') }}
              </NTag>
              <NProgress
                type="line"
                :percentage="embeddingStatus.progress"
                :show-indicator="true"
                :processing="!selectedPaused"
                style="width: 200px"
              />
              <NButton
                v-if="selectedDownloading"
                type="default"
                :disabled="!selectedModel"
                @click="handlePauseDownload"
              >
                <template #icon><NIcon><Pause /></NIcon></template>
                {{ t('settings.embeddingModelMgmt.pause') }}
              </NButton>
              <NButton
                v-if="selectedPaused"
                type="primary"
                :disabled="!selectedModel"
                @click="handleResumeDownload"
              >
                <template #icon><NIcon><Play /></NIcon></template>
                {{ t('settings.embeddingModelMgmt.resume') }}
              </NButton>
              <NButton
                type="error"
                secondary
                :disabled="!selectedModel"
                @click="handleCancelDownload"
              >
                <template #icon><NIcon><CloseCircle /></NIcon></template>
                {{ t('settings.embeddingModelMgmt.cancel') }}
              </NButton>
              <span class="muted" style="font-size: 12px">{{ embeddingStatus.message }}</span>
            </div>

            <!-- Idle / completed / failed / cancelled: status tag | info | actions -->
            <NSpace v-else align="center" :size="12">
              <NTag v-if="selectedInstalled" type="success" :bordered="false" round>
                {{ t('settings.embeddingModelMgmt.statusInstalled') }}
              </NTag>
              <NTag v-else-if="selectedFailed" type="error" :bordered="false" round>
                {{ t('settings.embeddingModelMgmt.statusFailed') }}
              </NTag>
              <NTag v-else-if="selectedCancelled" type="default" :bordered="false" round>
                {{ t('settings.embeddingModelMgmt.statusCancelled') }}
              </NTag>
              <NTag v-else type="default" :bordered="false" round>
                {{ t('settings.embeddingModelMgmt.statusNotInstalled') }}
              </NTag>

              <span class="muted" style="font-size: 12px">
                <template v-if="selectedInstalled">
                  {{ t('settings.embeddingModelMgmt.installedTip') }}
                </template>
                <template v-else-if="selectedCancelled">
                  {{ t('settings.embeddingModelMgmt.cancelledTip') }}
                </template>
                <template v-else>
                  {{ t('settings.embeddingModelMgmt.notInstalledTip') }}
                </template>
              </span>

              <NButton
                v-if="!selectedInstalled && !selectedActiveDownload"
                type="primary"
                :disabled="!selectedModel"
                @click="handleDownloadEmbedding"
              >
                <template #icon><NIcon><Download /></NIcon></template>
                {{ t('settings.embeddingModelMgmt.download') }}
              </NButton>

              <NButton
                v-if="selectedInstalled && !selectedActiveDownload"
                type="error"
                secondary
                :disabled="selectedIsConfigured"
                @click="handleDeleteEmbedding"
              >
                {{ t('settings.embeddingModelMgmt.delete') }}
              </NButton>
            </NSpace>
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
            <NAlert type="error" :bordered="false">
              <div style="font-size: 13px">{{ t('settings.embeddingModelMgmt.statusFailed') }}: {{ embeddingStatus.error }}</div>
            </NAlert>
          </NFormItem>

          <NFormItem v-if="reindexStatus.status === 'running'" :show-feedback="false">
            <NAlert
              type="warning"
              :bordered="false"
            >
              <div style="display: flex; align-items: center; gap: 10px; font-size: 13px; flex-wrap: wrap">
                <span style="white-space: nowrap">{{ t('settings.embeddingModelMgmt.reindexTitle') }}: {{ reindexPhaseLabel(reindexStatus.phase, reindexStatus.params) || reindexStatus.error }}</span>
                <NProgress
                  type="line"
                  :percentage="reindexStatus.progress"
                  :show-indicator="true"
                  :processing="true"
                  style="flex: 1; min-width: 120px"
                />
                <span class="muted" style="font-size: 12px; white-space: nowrap">{{ reindexStatus.current }} / {{ reindexStatus.total }}</span>
              </div>
            </NAlert>
          </NFormItem>

          <NAlert
            v-if="dimensionConflict && reindexStatus.status !== 'running'"
            type="warning"
            :bordered="false"
            style="margin-top: 8px"
          >
            <div style="font-size: 13px">{{ t('settings.embeddingModelMgmt.conflictTitle') }}: {{ dimensionConflict }}</div>
          </NAlert>
          
        </section>
      </NForm>
    </NCard>

    <NCard :bordered="false" class="settings-card" style="margin-top: 16px">
      <NForm label-placement="left" label-width="160">
        <!-- System Prompt -->
        <section id="system-prompt">
          <h3 class="section-title">{{ t('settings.systemPromptTitle') }}</h3>
          <p class="muted" style="margin: 0 0 16px;font-size: 13px">{{ t('settings.systemPromptDesc') }}</p>
          <NFormItem>
            <template #label>
              <span class="label-with-help">
                {{ t('settings.systemPrompt') }}
                <NTooltip trigger="hover" :width="300">
                  <template #trigger>
                    <NIcon :component="HelpCircle" size="14" class="help-icon" />
                  </template>
                  <span>{{ t('settings.systemPromptTip') }}</span>
                </NTooltip>
              </span>
            </template>
            <NInput
              v-model:value="systemPromptModel"
              type="textarea"
              :rows="10"
              :placeholder="t('settings.systemPrompt')"
              @input="clearTest"
              @change="scheduleSave(t('settings.systemPrompt'))"
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
                  <span>{{ t('settings.agentPromptLangTip') }}</span>
                </NTooltip>
              </span>
            </template>
            <NSpace align="center">
              <NSelect
                v-model:value="config.prompt_language"
                :options="promptLangOptions"
                size="small"
                style="width: 160px"
                @update:value="scheduleSave(t('settings.agentPromptLang'))"
              />
            </NSpace>
          </NFormItem>
        </section>

      </NForm>
    </NCard>

    <NCard :bordered="false" class="settings-card" style="margin-top: 16px">
      <section id="https">
        <h3 class="section-title">{{ t('settings.httpsTitle') }}</h3>
        <p class="muted" style="margin: 0 0 16px;font-size: 13px">{{ t('settings.httpsDesc') }}</p>
        <NForm label-placement="left" label-width="140">
          <NFormItem :label="t('settings.httpsEnableLabel')">
            <div style="display: flex; align-items: center; gap: 12px">
              <NSwitch v-model:value="httpsEnabled" @update:value="httpsDirty = true" />
              <span class="muted" style="font-size: 12px">
                <template v-if="httpsEnabled && httpsMeta">
                  {{ t('settings.httpsStatusOn') }} · {{ t('settings.httpsCertInfo', { subject: httpsMeta.subject, expires: httpsMeta.expires }) }}
                </template>
                <template v-else-if="httpsEnabled">
                  {{ t('settings.httpsStatusOn') }}
                </template>
                <template v-else>
                  {{ t('settings.httpsStatusOff') }}
                </template>
                <template v-if="httpsDirty"> · {{ t('settings.httpsUnsaved') }}</template>
                <br />{{ t('settings.httpsAccessHint') }}
              </span>
            </div>
          </NFormItem>
          <template v-if="httpsEnabled">
            <NFormItem :label="t('settings.httpsCertLabel')">
              <NInput
                v-model:value="httpsCert"
                type="textarea"
                :rows="6"
                :placeholder="t('settings.httpsCertPlaceholder')"
                @input="httpsDirty = true"
              />
            </NFormItem>
            <NFormItem :label="t('settings.httpsKeyLabel')">
              <NInput
                v-model:value="httpsKey"
                type="textarea"
                :rows="6"
                :placeholder="t('settings.httpsKeyPlaceholder')"
                @input="httpsDirty = true"
              />
            </NFormItem>
          </template>
          <NFormItem>
            <div style="display: flex; justify-content: flex-end; width: 100%">
              <NButton
                type="primary"
                :loading="httpsSaving"
                :disabled="httpsSaving || (httpsEnabled && (!httpsCert.trim() || !httpsKey.trim()))"
                @click="handleHttpsSave"
              >
                <template #icon><NIcon><Save /></NIcon></template>
                {{ t('settings.httpsSave') }}
              </NButton>
            </div>
          </NFormItem>
        </NForm>
      </section>
    </NCard>

    <NCard :bordered="false" class="settings-card" style="margin-top: 16px">
      <section id="sandbox-network">
        <h3 class="section-title">{{ t('settings.sandboxTitle') }}</h3>
        <p class="muted" style="margin: 0 0 16px;font-size: 13px">{{ t('settings.sandboxDesc') }}</p>
        <NForm label-placement="left" label-width="140">
          <NFormItem :label="t('settings.networkMode')">
            <NSelect v-model:value="sandboxConfig.sandbox_network_mode" :options="networkModeOptions" @update:value="scheduleSave(t('settings.networkMode'))" />
          </NFormItem>
          <NFormItem v-if="sandboxConfig.sandbox_network_mode === 'allowlist'" :label="t('settings.allowDomains')">
              <NInput
                v-model:value="sandboxConfig.sandbox_allow_domains"
                type="textarea"
                :rows="3"
                placeholder="api.github.com, raw.githubusercontent.com"
                @change="scheduleSave(t('settings.allowDomains'))"
              />
          </NFormItem>
        </NForm>
      </section>
    </NCard>

    <NCard :bordered="false" class="settings-card" style="margin-top: 16px">
      <section id="repl-auth">
        <h3 class="section-title">{{ t('settings.replAuthTitle') }}</h3>
        <p class="muted" style="margin: 0 0 16px;font-size: 13px">{{ t('settings.replAuthDesc') }}</p>
        <NForm label-placement="left" label-width="140">
          <NFormItem :label="t('settings.replAuthSecretLabel')">
            <NSpace vertical :size="8" style="width: 100%">
              <NInput
                v-model:value="replAuthSecret"
                type="password"
                show-password-on="click"
                readonly
                :placeholder="t('settings.replAuthPlaceholder')"
              />
              <NSpace :size="8">
                <NButton
                  type="primary"
                  :loading="replAuthGenerating"
                  :disabled="replAuthGenerating"
                  @click="handleReplAuthGenerate"
                >
                  <template #icon><NIcon><Refresh /></NIcon></template>
                  {{ t('settings.replAuthGenerate') }}
                </NButton>
                <NButton :disabled="!replAuthSecret.trim()" @click="handleReplAuthCopy">
                  <template #icon><NIcon><Copy /></NIcon></template>
                  {{ t('settings.replAuthCopy') }}
                </NButton>
              </NSpace>
              <span class="muted" style="font-size: 12px">
                <template v-if="replAuthPushed === true">
                  {{ t('settings.replAuthStatusOn') }}
                </template>
                <template v-else-if="replAuthPushed === false">
                  {{ t('settings.replAuthStatusRestart') }}
                </template>
                <template v-else>
                  {{ t('settings.replAuthStatus', { on: replAuthSecret.trim() ? t('settings.replAuthOn') : t('settings.replAuthOff') }) }}
                </template>
              </span>
            </NSpace>
          </NFormItem>
        </NForm>
      </section>
    </NCard>

    <NCard :bordered="false" class="settings-card" style="margin-top: 16px">
      <section id="jwt-auth">
        <h3 class="section-title">{{ t('settings.jwtAuthTitle') }}</h3>
        <p class="muted" style="margin: 0 0 16px;font-size: 13px">{{ t('settings.jwtAuthDesc') }}</p>
        <NForm label-placement="left" label-width="140">
          <NFormItem :label="t('settings.jwtAuthSecretLabel')">
            <NSpace vertical :size="8" style="width: 100%">
              <NInput
                v-model:value="jwtAuthSecret"
                type="password"
                show-password-on="click"
                readonly
                :placeholder="t('settings.jwtAuthPlaceholder')"
              />
              <NSpace :size="8">
                <NButton
                  type="primary"
                  :loading="jwtAuthGenerating"
                  :disabled="jwtAuthGenerating"
                  @click="handleJwtAuthGenerate"
                >
                  <template #icon><NIcon><Refresh /></NIcon></template>
                  {{ t('settings.jwtAuthGenerate') }}
                </NButton>
                <NButton :disabled="!jwtAuthSecret.trim()" @click="handleJwtAuthCopy">
                  <template #icon><NIcon><Copy /></NIcon></template>
                  {{ t('settings.jwtAuthCopy') }}
                </NButton>
              </NSpace>
            </NSpace>
          </NFormItem>
        </NForm>
      </section>
    </NCard>

    <NCard :bordered="false" class="settings-card" style="margin-top: 16px">
      <section id="plugins">
        <div style="display: flex; align-items: center; gap: 8px">
          <h3 class="section-title" style="margin: 0">{{ t('settings.pluginsTitle') }}</h3>
          <NTag v-if="!pluginStats.loading" size="small" type="info">
            {{ t('plugins.enabledTag', { enabled: pluginStats.enabled, total: pluginStats.total }) }}
          </NTag>
          <NButton size="small" secondary style="margin-left: auto" @click="onPluginRefresh">
            <template #icon><NIcon><Refresh /></NIcon></template>
            {{ t('plugins.refreshCache') }}
          </NButton>
        </div>
        <p class="muted" style="margin: 0 0 16px;font-size: 13px">{{ t('settings.pluginsDesc') }}</p>
        <PluginManagementSection ref="pluginSectionRef" @stats="onPluginStats" />
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

.label-with-help { display: inline-flex; align-items: center; gap: 4px; }
.help-icon { color: var(--color-text-muted); cursor: help; transition: color 0.15s; }
.help-icon:hover { color: var(--color-primary); }

.test-result {
  display: inline-flex; align-items: flex-start; gap: 6px;
  padding: 4px 12px; border-radius: 6px; font-size: var(--text-sm);
  white-space: normal; word-break: break-word; overflow-wrap: anywhere; line-height: 1.4;
}
.test-result :deep(.n-icon) { flex-shrink: 0; margin-top: 1px; }
.test-ok { background: rgba(34,197,94,0.1); color: #16a34a; }
.test-fail { background: rgba(239,68,68,0.1); color: #dc2626; }

.save-status {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 13px; line-height: 1; white-space: nowrap;
}
.save-status.saving { color: var(--color-text-muted); }
.save-status.saved { color: #16a34a; }
.save-status.error { color: #dc2626; }
.save-status .n-icon { vertical-align: -2px; }

</style>

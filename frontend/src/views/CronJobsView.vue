<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NButton, NForm, NFormItem, NInput,
  NCard, NIcon, useMessage, NSpace, NPopconfirm, NDrawer, NDrawerContent,
  NInputNumber, NTag, NSpin, NTooltip, NDescriptions, NDescriptionsItem,
  NEmpty, NSelect,
} from 'naive-ui'
import { Add, Trash, Create, Play, Time, Ban, CheckmarkCircle, Search } from '@vicons/ionicons5'
import StatusToggle from '@/components/common/StatusToggle.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import AppModal from '@/components/common/AppModal.vue'
import AppPagination from '@/components/common/AppPagination.vue'
import {
  listCronJobs, createCronJob, updateCronJob, deleteCronJob,
  toggleCronJob, runCronJobNow, listCronJobRuns,
} from '@/api/cronJobs'
import type { CronJob, CronJobCreatePayload, CronJobRun } from '@/types'
import { parseUtcTs } from '@/utils/datetime'

const message = useMessage()
const { t } = useI18n()

// ── Data ──

const jobs = ref<CronJob[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = 20

// Filters — search + status (mirrors DocumentManage.vue)
const search = ref('')
const filterStatus = ref<string>('all')
const statusOptions = [
  { label: t('cron.status.all'), value: 'all' },
  { label: t('cron.status.scheduled'), value: 'scheduled' },
  { label: t('cron.status.running'), value: 'running' },
  { label: t('cron.status.paused'), value: 'paused' },
  { label: t('cron.status.completed'), value: 'completed' },
  { label: t('cron.status.failed'), value: 'failed' },
]

const showModal = ref(false)
const editing = ref<CronJob | null>(null)
// Detect the browser's local IANA timezone so new cron jobs are scheduled in
// the user's wall-clock time by default (previously hardcoded to UTC, which
// shifted schedules by the local offset).
const localTz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
const form = ref<CronJobCreatePayload & { id?: string }>({
  name: '',
  description: '',
  cron_expr: '',
  timezone: localTz,
  max_runs: null,
  task_content: '',
  kb_id: '',
  skill_id: '',
})

const detailJob = ref<CronJob | null>(null)
const showDetail = ref(false)

const runsDrawerOpen = ref(false)
const selectedJobId = ref('')
const runs = ref<CronJobRun[]>([])
const runsLoading = ref(false)
const runningId = ref('')

// ── Button style constants (consistent with SkillsView / DocumentManage) ──
const yellowStyle = {
  '--n-text-color': '#f59e0b', '--n-border': '1px solid #f59e0b',
  '--n-border-hover': '1px solid #d97706', '--n-border-pressed': '1px solid #d97706',
  '--n-text-color-hover': '#d97706', '--n-text-color-pressed': '#d97706',
}
const greenStyle = {
  '--n-text-color': '#22c55e', '--n-border': '1px solid #22c55e',
  '--n-border-hover': '1px solid #16a34a', '--n-border-pressed': '1px solid #16a34a',
  '--n-text-color-hover': '#16a34a', '--n-text-color-pressed': '#16a34a',
}
const redStyle = {
  '--n-text-color': '#ef4444', '--n-border': '1px solid #ef4444',
  '--n-border-hover': '1px solid #dc2626', '--n-border-pressed': '1px solid #dc2626',
  '--n-text-color-hover': '#dc2626', '--n-text-color-pressed': '#dc2626',
}

// ── Load ──

async function load() {
  loading.value = true
  try {
    const data = await listCronJobs(
      page.value, pageSize,
      search.value || undefined,
      filterStatus.value === 'all' ? undefined : filterStatus.value,
    )
    jobs.value = data.items
    total.value = data.total
  } catch (e: any) {
    message.error(e.message || t('cron.loadFailed'))
  } finally {
    loading.value = false
  }
}

function onPageChange(p: number) {
  page.value = p
  load()
}

function onSearch() {
  page.value = 1
  load()
}

function resetFilters() {
  search.value = ''
  filterStatus.value = 'all'
  page.value = 1
  load()
}

function refreshDetail() {
  if (!detailJob.value) return
  const u = jobs.value.find(j => j.id === detailJob.value!.id)
  if (u) detailJob.value = u
  else detailJob.value = null
}

onMounted(load)

// ── CRUD ──

function openCreate() {
  editing.value = null
  form.value = {
    name: '',
    description: '',
    cron_expr: '',
    timezone: localTz,
    max_runs: null,
    task_content: '',
    kb_id: '',
    skill_id: '',
  }
  showModal.value = true
}

function openEdit(job: CronJob) {
  editing.value = job
  form.value = {
    id: job.id,
    name: job.name,
    description: job.description || '',
    cron_expr: job.cron_expr,
    timezone: job.timezone,
    max_runs: job.max_runs ?? null,
    task_content: job.task_content,
    kb_id: job.kb_id || '',
    skill_id: job.skill_id || '',
  }
  showModal.value = true
}

async function handleSave() {
  try {
    const payload: CronJobCreatePayload = {
      name: form.value.name,
      description: form.value.description || undefined,
      cron_expr: form.value.cron_expr,
      timezone: form.value.timezone,
      max_runs: form.value.max_runs,
      task_content: form.value.task_content,
      kb_id: form.value.kb_id || undefined,
      skill_id: form.value.skill_id || undefined,
    }
    if (editing.value) {
      await updateCronJob(editing.value.id, payload)
      message.success(t('cron.updated'))
    } else {
      await createCronJob(payload)
      message.success(t('cron.created'))
    }
    showModal.value = false
    await load()
    refreshDetail()
  } catch (e: any) {
    message.error(e.message || t('cron.saveFailed'))
  }
}

async function handleDelete(job: CronJob) {
  try {
    await deleteCronJob(job.id)
    message.success(t('cron.deleted'))
    if (detailJob.value?.id === job.id) {
      showDetail.value = false
      detailJob.value = null
    }
    await load()
  } catch (e: any) {
    message.error(e.message || t('cron.deleteFailed'))
  }
}

async function handleToggle(job: CronJob) {
  try {
    await toggleCronJob(job.id)
    message.success(t('cron.statusSwitched'))
    await load()
    refreshDetail()
  } catch (e: any) {
    message.error(e.message || t('cron.switchFailed'))
  }
}

async function handleRunNow(job: CronJob) {
  runningId.value = job.id
  try {
    const res = await runCronJobNow(job.id)
    message.success(res.result ? t('cron.execCompleteWith', { result: res.result.slice(0, 100) }) : t('cron.execComplete'))
    await load()
    refreshDetail()
  } catch (e: any) {
    message.error(e.message || t('cron.execFailed'))
  } finally {
    runningId.value = ''
  }
}

function openDetail(job: CronJob) {
  detailJob.value = job
  showDetail.value = true
}

async function openRuns(job: CronJob) {
  selectedJobId.value = job.id
  runsDrawerOpen.value = true
  runsLoading.value = true
  try {
    const data = await listCronJobRuns(job.id, 1, 50)
    runs.value = data.items
  } catch (e: any) {
    message.error(e.message || t('cron.loadLogFailed'))
  } finally {
    runsLoading.value = false
  }
}

// ── Helpers ──

function formatTime(t?: string | null) {
  const d = parseUtcTs(t)
  if (!d) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function statusType(status: string) {
  switch (status) {
    case 'scheduled': return 'success'
    case 'running': return 'warning'
    case 'paused': return 'default'
    case 'completed': return 'info'
    case 'failed': return 'error'
    default: return 'default'
  }
}

function statusLabel(status: string) {
  const map: Record<string, string> = {
    scheduled: t('cron.status.scheduled'),
    running: t('cron.status.running'),
    paused: t('cron.status.paused'),
    completed: t('cron.status.completed'),
    failed: t('cron.statusLabel.failed'),
  }
  return map[status] || status
}

function isPaused(job: CronJob) {
  return job.status === 'paused' || job.status === 'failed'
}
</script>

<template>
  <div class="page-container pm-flex">
    <PageHeader :title="t('cron.title')" :icon="Time">
      <template #badge v-if="total > 0">{{ total }}</template>
      <template #actions>
        <NTooltip trigger="hover">
          <template #trigger>
            <NButton type="primary" size="small" @click="openCreate">
              <template #icon><NIcon size="14"><Add /></NIcon></template>
              {{ t('cron.createNewJob') }}
            </NButton>
          </template>
          {{ t('cron.nlCreateHint') }}
        </NTooltip>
      </template>
    </PageHeader>

    <!-- Filters -->
    <div class="dm-filters">
      <NInput v-model:value="search" :placeholder="t('cron.searchPlaceholder')" clearable size="small" @keyup.enter="onSearch" style="flex:1">
        <template #prefix><NIcon><Search /></NIcon></template>
      </NInput>
      <NButton size="small" type="primary" @click="onSearch">
        <template #icon><NIcon><Search /></NIcon></template>
        {{ t('common.search') }}
      </NButton>
      <NSelect v-model:value="filterStatus" :options="statusOptions" :placeholder="t('common.status')" size="small" style="width:130px" @update:value="onSearch" />
      <NButton size="small" @click="resetFilters" secondary>{{ t('common.reset') }}</NButton>
    </div>

    <NSpin :show="loading" class="pm-scroll">
      <NEmpty v-if="!loading && jobs.length === 0" :description="t('cron.empty')" />
      <div class="cj-list" v-if="jobs.length > 0">
        <NCard
          v-for="job in jobs"
          :key="job.id"
          size="small"
          :class="['cj-card', { 'cj-card-disabled': isPaused(job) }]"
          hoverable
          role="button"
          tabindex="0"
          @click="openDetail(job)"
          @keydown.enter.prevent="openDetail(job)"
          @keydown.space.prevent="openDetail(job)"
        >
          <div class="cj-card-header">
            <div class="cj-card-title-wrap">
              <span class="cj-name" :title="job.name">{{ job.name }}</span>
              <NTag :type="statusType(job.status)" size="tiny" :bordered="false">{{ statusLabel(job.status) }}</NTag>
            </div>
            <div class="cj-card-toggle" @click.stop>
              <StatusToggle
                :value="!isPaused(job)"
                @update:value="() => handleToggle(job)"
              />
            </div>
          </div>

          <div class="cj-card-desc" v-if="job.description">{{ job.description }}</div>

          <div class="cj-card-meta">
            <span class="cj-meta-label">{{ t('cron.runCount') }}</span>
            <span class="cj-meta-value">{{ job.run_count }}</span>
          </div>

          <div class="cj-card-meta">
            <span class="cj-meta-label">{{ t('cron.nextRun') }}</span>
            <span class="cj-meta-value">{{ formatTime(job.next_run_at) }}</span>
          </div>

          <template #footer>
            <NSpace justify="end">
              <NButton size="small" :loading="runningId === job.id" @click.stop="handleRunNow(job)">
                <template #icon><NIcon><Play /></NIcon></template>
                {{ t('cron.runNow') }}
              </NButton>
            </NSpace>
          </template>
        </NCard>
      </div>

    </NSpin>

    <AppPagination always-show :page="page" :page-size="pageSize" :item-count="total" @update:page="onPageChange" />

    <!-- Detail Modal -->
    <AppModal v-model:show="showDetail" :title="t('cron.detailTitle')" size="detail">
      <NDescriptions v-if="detailJob" :column="1" label-placement="left" bordered>
        <NDescriptionsItem :label="t('common.name')">{{ detailJob.name }}</NDescriptionsItem>
        <NDescriptionsItem :label="t('common.status')">
          <NTag :type="statusType(detailJob.status)" size="tiny" :bordered="false">{{ statusLabel(detailJob.status) }}</NTag>
        </NDescriptionsItem>
        <NDescriptionsItem :label="t('cron.description')">{{ detailJob.description || '—' }}</NDescriptionsItem>
        <NDescriptionsItem label="Crontab">{{ detailJob.cron_expr }}</NDescriptionsItem>
        <NDescriptionsItem :label="t('cron.timezone')">{{ detailJob.timezone }}</NDescriptionsItem>
        <NDescriptionsItem :label="t('cron.maxRuns')">{{ detailJob.max_runs ?? t('cron.unlimited') }}</NDescriptionsItem>
        <NDescriptionsItem :label="t('cron.runCount')">{{ detailJob.run_count }}</NDescriptionsItem>
        <NDescriptionsItem :label="t('cron.taskContent')">{{ detailJob.task_content }}</NDescriptionsItem>
        <NDescriptionsItem :label="t('cron.kbId')">{{ detailJob.kb_id || '—' }}</NDescriptionsItem>
        <NDescriptionsItem :label="t('cron.skillId')">{{ detailJob.skill_id || '—' }}</NDescriptionsItem>
        <NDescriptionsItem :label="t('cron.nextRun')">{{ formatTime(detailJob.next_run_at) }}</NDescriptionsItem>
        <NDescriptionsItem :label="t('cron.lastRun')">{{ formatTime(detailJob.last_run_at) }}</NDescriptionsItem>
        <NDescriptionsItem :label="t('common.createdAt')">{{ formatTime(detailJob.created_at) }}</NDescriptionsItem>
        <NDescriptionsItem :label="t('common.updatedAt')">{{ formatTime(detailJob.updated_at) }}</NDescriptionsItem>
        <NDescriptionsItem :label="t('cron.jobId')">{{ detailJob.id }}</NDescriptionsItem>
      </NDescriptions>

      <template #footer>
        <NSpace justify="end">
          <NButton size="small" v-if="detailJob" @click="openEdit(detailJob)">
            <template #icon><NIcon><Create /></NIcon></template>
            {{ t('common.edit') }}
          </NButton>
          <NButton size="small" v-if="detailJob" :loading="runningId === detailJob.id" @click="handleRunNow(detailJob)">
            <template #icon><NIcon><Play /></NIcon></template>
            {{ t('cron.runNow') }}
          </NButton>
          <NButton size="small" v-if="detailJob" @click="openRuns(detailJob)">
            <template #icon><NIcon><Time /></NIcon></template>
            {{ t('cron.logs') }}
          </NButton>
          <NButton size="small" v-if="detailJob" @click="handleToggle(detailJob)" :style="isPaused(detailJob) ? greenStyle : yellowStyle">
            <template #icon>
              <NIcon>
                <CheckmarkCircle v-if="isPaused(detailJob)" />
                <Ban v-else />
              </NIcon>
            </template>
            {{ isPaused(detailJob) ? t('common.enable') : t('cron.pause') }}
          </NButton>
          <NPopconfirm v-if="detailJob" @positive-click="handleDelete(detailJob)">
            <template #trigger>
              <NButton size="small" :style="redStyle">
                <template #icon><NIcon><Trash /></NIcon></template>
                {{ t('common.delete') }}
              </NButton>
            </template>
            {{ t('cron.confirmDelete') }}
          </NPopconfirm>
        </NSpace>
      </template>
    </AppModal>

    <!-- Create/Edit Modal -->
    <AppModal v-model:show="showModal" :title="editing ? t('cron.editJobTitle') : t('cron.createJobTitle')" size="detail">
      <NForm label-placement="left" label-width="100">
        <NFormItem :label="t('cron.jobName')" required>
          <NInput v-model:value="form.name" :placeholder="t('cron.jobNamePlaceholder')" />
        </NFormItem>
        <NFormItem :label="t('cron.description')">
          <NInput v-model:value="form.description" type="textarea" :placeholder="t('cron.descriptionPlaceholder')" />
        </NFormItem>
        <NFormItem label="Crontab" required>
          <NInput v-model:value="form.cron_expr" :placeholder="t('cron.cronExprPlaceholder')" />
        </NFormItem>
        <NFormItem :label="t('cron.timezone')">
          <NInput v-model:value="form.timezone" :placeholder="localTz" />
        </NFormItem>
        <NFormItem :label="t('cron.maxRuns')">
          <NInputNumber v-model:value="form.max_runs" :min="1" :show-button="false" :placeholder="t('cron.maxRunsPlaceholder')" style="width: 100%" />
        </NFormItem>
        <NFormItem :label="t('cron.taskContent')" required>
          <NInput v-model:value="form.task_content" type="textarea" :rows="4" :placeholder="t('cron.taskContentPlaceholder')" />
        </NFormItem>
        <NFormItem :label="t('cron.kbId')">
          <NInput v-model:value="form.kb_id" :placeholder="t('cron.optional')" />
        </NFormItem>
        <NFormItem :label="t('cron.skillId')">
          <NInput v-model:value="form.skill_id" :placeholder="t('cron.optional')" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showModal = false">{{ t('common.cancel') }}</NButton>
          <NButton type="primary" @click="handleSave">{{ t('common.save') }}</NButton>
        </NSpace>
      </template>
    </AppModal>

    <!-- Runs Drawer -->
    <NDrawer v-model:show="runsDrawerOpen" width="720" placement="right">
      <NDrawerContent :title="t('cron.execLog')" closable>
        <NSpin :show="runsLoading">
          <div v-if="runs.length === 0" class="empty">{{ t('cron.noRunRecords') }}</div>
          <div v-else class="run-list">
            <NCard v-for="run in runs" :key="run.id" size="small" style="margin-bottom: 12px">
              <div class="run-meta">
                <NTag :type="run.status === 'success' ? 'success' : 'error'">{{ run.status }}</NTag>
                <span class="run-time">{{ formatTime(run.started_at) }}</span>
              </div>
              <pre v-if="run.output" class="run-output">{{ run.output }}</pre>
              <div v-if="run.error" class="run-error">{{ t('cron.errorPrefix') }}{{ run.error }}</div>
            </NCard>
          </div>
        </NSpin>
      </NDrawerContent>
    </NDrawer>
  </div>
</template>

<style scoped>
/* Cron job card grid (style reference: SkillsView.vue) */
.dm-filters { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.cj-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.cj-card {
  cursor: pointer;
  background: var(--color-card-bg);
  --n-color: var(--color-card-bg);
  border: 1px solid var(--color-card-border);
  --n-border-color: var(--color-card-border);
  box-shadow: var(--shadow-sm);
  transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease;
}
.cj-card:hover {
  border-color: var(--color-primary);
  box-shadow: var(--shadow);
  transform: translateY(-1px);
}
.cj-card:focus-visible {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-soft);
}
.cj-card-disabled {
  background: var(--color-card-bg-disabled);
  --n-color: var(--color-card-bg-disabled);
  cursor: not-allowed;
}
.cj-card-disabled:hover {
  border-color: var(--color-card-border);
  box-shadow: var(--shadow-sm);
  transform: none;
}
.cj-card :deep(.n-card__footer) {
  padding-top: 6px;
}
.cj-card-header {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 12px;
}
.cj-card-title-wrap {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.cj-name {
  font-weight: 600;
  font-size: var(--text-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 160px;
}
.cj-card-toggle {
  flex-shrink: 0;
}
.cj-card-desc {
  font-size: 12px;
  color: #1f2937;
  line-height: 1.5;
  margin-bottom: 10px;
  word-break: break-word;
}
html.dark .cj-card-desc {
  color: #e5e7eb;
}
.cj-card-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-xs);
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.cj-card-meta:last-child {
  margin-bottom: 2px;
}
.cj-meta-label {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-text);
  flex-shrink: 0;
}
.cj-meta-value {
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.empty {
  color: var(--color-text-muted);
  text-align: center;
  padding: 32px 0;
}
.run-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.run-time {
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.run-output {
  background: var(--color-surface);
  padding: 12px;
  border-radius: var(--radius);
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: var(--text-sm);
  max-height: 240px;
  overflow-y: auto;
}
.run-error {
  color: var(--color-error);
  margin-top: 8px;
  font-size: var(--text-sm);
}
/* Sticky footer pagination: the list scrolls, the pager stays pinned at the
   bottom of the viewport (mirrors WorkspaceView.vue). */
.pm-flex {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.pm-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
</style>

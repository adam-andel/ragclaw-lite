<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  NButton, NForm, NFormItem, NInput,
  NCard, NIcon, useMessage, NSpace, NPopconfirm, NDrawer, NDrawerContent,
  NInputNumber, NTag, NSpin, NTooltip, NDescriptions, NDescriptionsItem,
  NEmpty,
} from 'naive-ui'
import { Add, Trash, Create, Play, Time, Ban, CheckmarkCircle } from '@vicons/ionicons5'
import StatusToggle from '@/components/common/StatusToggle.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import AppModal from '@/components/common/AppModal.vue'
import AppPagination from '@/components/common/AppPagination.vue'
import {
  listCronJobs, createCronJob, updateCronJob, deleteCronJob,
  toggleCronJob, runCronJobNow, listCronJobRuns,
} from '@/api/cronJobs'
import type { CronJob, CronJobCreatePayload, CronJobRun } from '@/types'

const message = useMessage()

// ── Data ──

const jobs = ref<CronJob[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = 20

const showModal = ref(false)
const editing = ref<CronJob | null>(null)
const form = ref<CronJobCreatePayload & { id?: string }>({
  name: '',
  description: '',
  cron_expr: '',
  timezone: 'UTC',
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
    const data = await listCronJobs(page.value, pageSize)
    jobs.value = data.items
    total.value = data.total
  } catch (e: any) {
    message.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function onPageChange(p: number) {
  page.value = p
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
    timezone: 'UTC',
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
      message.success('定时任务已更新')
    } else {
      await createCronJob(payload)
      message.success('定时任务已创建')
    }
    showModal.value = false
    await load()
    refreshDetail()
  } catch (e: any) {
    message.error(e.message || '保存失败')
  }
}

async function handleDelete(job: CronJob) {
  try {
    await deleteCronJob(job.id)
    message.success('定时任务已删除')
    if (detailJob.value?.id === job.id) {
      showDetail.value = false
      detailJob.value = null
    }
    await load()
  } catch (e: any) {
    message.error(e.message || '删除失败')
  }
}

async function handleToggle(job: CronJob) {
  try {
    await toggleCronJob(job.id)
    message.success('状态已切换')
    await load()
    refreshDetail()
  } catch (e: any) {
    message.error(e.message || '切换失败')
  }
}

async function handleRunNow(job: CronJob) {
  runningId.value = job.id
  try {
    const res = await runCronJobNow(job.id)
    message.success(res.result ? `执行完成：${res.result.slice(0, 100)}` : '执行完成')
    await load()
    refreshDetail()
  } catch (e: any) {
    message.error(e.message || '执行失败')
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
    message.error(e.message || '加载日志失败')
  } finally {
    runsLoading.value = false
  }
}

// ── Helpers ──

function formatTime(t?: string | null) {
  if (!t) return '—'
  const d = new Date(t)
  if (isNaN(d.getTime())) return t
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
    scheduled: '已计划',
    running: '执行中',
    paused: '已暂停',
    completed: '已完成',
    failed: '失败',
  }
  return map[status] || status
}

function isPaused(job: CronJob) {
  return job.status === 'paused' || job.status === 'failed'
}
</script>

<template>
  <div class="page-container">
    <PageHeader title="定时任务管理" :icon="Time">
      <template #badge v-if="total > 0">{{ total }}</template>
      <template #actions>
        <NTooltip trigger="hover">
          <template #trigger>
            <NButton type="primary" size="small" @click="openCreate">
              <template #icon><NIcon size="14"><Add /></NIcon></template>
              新建定时任务
            </NButton>
          </template>
          可在对话中用自然语言创建定时任务
        </NTooltip>
      </template>
    </PageHeader>

    <NSpin :show="loading">
      <NEmpty v-if="!loading && jobs.length === 0" description="暂无定时任务" />
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

          <div class="cj-card-row">
            <span class="cj-card-label">执行次数</span>
            <span class="cj-meta">{{ job.run_count }}</span>
          </div>

          <div class="cj-card-row">
            <span class="cj-card-label">下次执行</span>
            <span class="cj-meta">{{ formatTime(job.next_run_at) }}</span>
          </div>

          <template #footer>
            <NSpace justify="end">
              <NButton size="small" :loading="runningId === job.id" @click.stop="handleRunNow(job)">
                <template #icon><NIcon><Play /></NIcon></template>
                立即执行
              </NButton>
            </NSpace>
          </template>
        </NCard>
      </div>

      <AppPagination :page="page" :page-size="pageSize" :item-count="total" @update:page="onPageChange" />
    </NSpin>

    <!-- Detail Modal -->
    <AppModal v-model:show="showDetail" title="定时任务详情" size="detail">
      <NDescriptions v-if="detailJob" :column="1" label-placement="left" bordered>
        <NDescriptionsItem label="名称">{{ detailJob.name }}</NDescriptionsItem>
        <NDescriptionsItem label="状态">
          <NTag :type="statusType(detailJob.status)" size="tiny" :bordered="false">{{ statusLabel(detailJob.status) }}</NTag>
        </NDescriptionsItem>
        <NDescriptionsItem label="描述">{{ detailJob.description || '—' }}</NDescriptionsItem>
        <NDescriptionsItem label="Crontab">{{ detailJob.cron_expr }}</NDescriptionsItem>
        <NDescriptionsItem label="时区">{{ detailJob.timezone }}</NDescriptionsItem>
        <NDescriptionsItem label="最大执行次数">{{ detailJob.max_runs ?? '无限' }}</NDescriptionsItem>
        <NDescriptionsItem label="执行次数">{{ detailJob.run_count }}</NDescriptionsItem>
        <NDescriptionsItem label="任务内容">{{ detailJob.task_content }}</NDescriptionsItem>
        <NDescriptionsItem label="知识库 ID">{{ detailJob.kb_id || '—' }}</NDescriptionsItem>
        <NDescriptionsItem label="技能 ID">{{ detailJob.skill_id || '—' }}</NDescriptionsItem>
        <NDescriptionsItem label="下次执行">{{ formatTime(detailJob.next_run_at) }}</NDescriptionsItem>
        <NDescriptionsItem label="最后执行">{{ formatTime(detailJob.last_run_at) }}</NDescriptionsItem>
        <NDescriptionsItem label="创建时间">{{ formatTime(detailJob.created_at) }}</NDescriptionsItem>
        <NDescriptionsItem label="更新时间">{{ formatTime(detailJob.updated_at) }}</NDescriptionsItem>
        <NDescriptionsItem label="任务 ID">{{ detailJob.id }}</NDescriptionsItem>
      </NDescriptions>

      <template #footer>
        <NSpace justify="end">
          <NButton size="small" v-if="detailJob" @click="openEdit(detailJob)">
            <template #icon><NIcon><Create /></NIcon></template>
            编辑
          </NButton>
          <NButton size="small" v-if="detailJob" :loading="runningId === detailJob.id" @click="handleRunNow(detailJob)">
            <template #icon><NIcon><Play /></NIcon></template>
            立即执行
          </NButton>
          <NButton size="small" v-if="detailJob" @click="openRuns(detailJob)">
            <template #icon><NIcon><Time /></NIcon></template>
            日志
          </NButton>
          <NButton size="small" v-if="detailJob" @click="handleToggle(detailJob)" :style="isPaused(detailJob) ? greenStyle : yellowStyle">
            <template #icon>
              <NIcon>
                <CheckmarkCircle v-if="isPaused(detailJob)" />
                <Ban v-else />
              </NIcon>
            </template>
            {{ isPaused(detailJob) ? '启用' : '暂停' }}
          </NButton>
          <NPopconfirm v-if="detailJob" @positive-click="handleDelete(detailJob)">
            <template #trigger>
              <NButton size="small" :style="redStyle">
                <template #icon><NIcon><Trash /></NIcon></template>
                删除
              </NButton>
            </template>
            确定删除该定时任务？
          </NPopconfirm>
        </NSpace>
      </template>
    </AppModal>

    <!-- Create/Edit Modal -->
    <AppModal v-model:show="showModal" :title="editing ? '编辑定时任务' : '新建定时任务'" size="detail">
      <NForm label-placement="left" label-width="100">
        <NFormItem label="任务名称" required>
          <NInput v-model:value="form.name" placeholder="例如：每日晨报" />
        </NFormItem>
        <NFormItem label="描述">
          <NInput v-model:value="form.description" type="textarea" placeholder="可选描述" />
        </NFormItem>
        <NFormItem label="Crontab" required>
          <NInput v-model:value="form.cron_expr" placeholder="例如：0 9 * * *" />
        </NFormItem>
        <NFormItem label="时区">
          <NInput v-model:value="form.timezone" placeholder="UTC" disabled />
        </NFormItem>
        <NFormItem label="最大执行次数">
          <NInputNumber v-model:value="form.max_runs" :min="1" :show-button="false" placeholder="留空表示无限次" style="width: 100%" />
        </NFormItem>
        <NFormItem label="任务内容" required>
          <NInput v-model:value="form.task_content" type="textarea" :rows="4" placeholder="用自然语言描述要执行的任务，例如：总结昨日上传的文档并生成 CSV 报表" />
        </NFormItem>
        <NFormItem label="知识库 ID">
          <NInput v-model:value="form.kb_id" placeholder="可选" />
        </NFormItem>
        <NFormItem label="技能 ID">
          <NInput v-model:value="form.skill_id" placeholder="可选" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showModal = false">取消</NButton>
          <NButton type="primary" @click="handleSave">保存</NButton>
        </NSpace>
      </template>
    </AppModal>

    <!-- Runs Drawer -->
    <NDrawer v-model:show="runsDrawerOpen" width="720" placement="right">
      <NDrawerContent title="执行日志" closable>
        <NSpin :show="runsLoading">
          <div v-if="runs.length === 0" class="empty">暂无执行记录</div>
          <div v-else class="run-list">
            <NCard v-for="run in runs" :key="run.id" size="small" style="margin-bottom: 12px">
              <div class="run-meta">
                <NTag :type="run.status === 'success' ? 'success' : 'error'">{{ run.status }}</NTag>
                <span class="run-time">{{ run.started_at }}</span>
              </div>
              <pre v-if="run.output" class="run-output">{{ run.output }}</pre>
              <div v-if="run.error" class="run-error">错误：{{ run.error }}</div>
            </NCard>
          </div>
        </NSpin>
      </NDrawerContent>
    </NDrawer>
  </div>
</template>

<style scoped>
/* Cron job card grid (style reference: SkillsView.vue) */
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
.cj-card-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--text-xs);
  margin-bottom: 8px;
}
.cj-card-row:last-child {
  margin-bottom: 2px;
}
.cj-card-label {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-text);
  flex-shrink: 0;
  width: 56px;
}
.cj-meta {
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
</style>

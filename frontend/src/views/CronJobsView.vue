<script setup lang="ts">
import { ref, onMounted, h } from 'vue'
import {
  NDataTable, NButton, NModal, NForm, NFormItem, NInput, NSwitch,
  NCard, NIcon, useMessage, NSpace, NPopconfirm, NDrawer, NDrawerContent,
  NInputNumber, NTag, NSelect, NSpin,
} from 'naive-ui'
import { Add, Trash, Create, Flash, Play, Time, Refresh } from '@vicons/ionicons5'
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

const runsDrawerOpen = ref(false)
const selectedJobId = ref('')
const runs = ref<CronJobRun[]>([])
const runsLoading = ref(false)
const runningId = ref('')

// ── Load ──

async function load() {
  loading.value = true
  try {
    const data = await listCronJobs(page.value, 20)
    jobs.value = data.items
    total.value = data.total
  } catch (e: any) {
    message.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
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
  } catch (e: any) {
    message.error(e.message || '保存失败')
  }
}

async function handleDelete(job: CronJob) {
  try {
    await deleteCronJob(job.id)
    message.success('定时任务已删除')
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
  } catch (e: any) {
    message.error(e.message || '执行失败')
  } finally {
    runningId.value = ''
  }
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

// ── Table ──

const columns = [
  { title: '名称', key: 'name', ellipsis: { tooltip: true } },
  { title: 'Crontab', key: 'cron_expr', width: 140 },
  { title: '状态', key: 'status', width: 100, render: (row: CronJob) => h(NTag, { type: statusType(row.status) }, { default: () => statusLabel(row.status) }) },
  { title: '执行次数', key: 'run_count', width: 90 },
  { title: '下次执行', key: 'next_run_at', width: 170 },
  { title: '最后执行', key: 'last_run_at', width: 170 },
  {
    title: '操作',
    key: 'actions',
    width: 280,
    render: (row: CronJob) =>
      h(NSpace, { size: 'small' }, {
        default: () => [
          h(NButton, { size: 'small', onClick: () => openEdit(row) }, { default: () => '编辑', icon: () => h(NIcon, null, { default: () => h(Create) }) }),
          h(NButton, { size: 'small', onClick: () => handleToggle(row) }, { default: () => (row.status === 'paused' || row.status === 'failed' ? '启用' : '暂停') }),
          h(NButton, { size: 'small', loading: runningId.value === row.id, onClick: () => handleRunNow(row) }, { default: () => '立即执行', icon: () => h(NIcon, null, { default: () => h(Play) }) }),
          h(NButton, { size: 'small', onClick: () => openRuns(row) }, { default: () => '日志', icon: () => h(NIcon, null, { default: () => h(Time) }) }),
          h(NPopconfirm, { onPositiveClick: () => handleDelete(row) }, {
            trigger: () => h(NButton, { size: 'small', type: 'error' }, { default: () => '删除', icon: () => h(NIcon, null, { default: () => h(Trash) }) }),
            default: () => '确定删除该定时任务？',
          }),
        ],
      }),
  },
]
</script>

<template>
  <div>
    <NCard title="定时任务管理">
      <template #header-extra>
        <NButton type="primary" @click="openCreate">
          <template #icon><NIcon><Add /></NIcon></template>
          新建定时任务
        </NButton>
      </template>

      <NDataTable
        :columns="columns"
        :data="jobs"
        :loading="loading"
        :pagination="{ page: page, pageSize: 20, itemCount: total, onChange: (p) => { page = p; load() } }"
        :row-key="(row: CronJob) => row.id"
        striped
      />
    </NCard>

    <!-- Create/Edit Modal -->
    <NModal v-model:show="showModal" :title="editing ? '编辑定时任务' : '新建定时任务'" preset="card" style="width: 640px">
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
          <NInput v-model:value="form.timezone" placeholder="UTC" />
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
    </NModal>

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

export default {
  // ── Page / titles ──
  title: '定时任务管理',
  detailTitle: '定时任务详情',
  editJobTitle: '编辑定时任务',
  createJobTitle: '新建定时任务',
  createNewJob: '新建定时任务',
  execLog: '执行日志',

  // ── Header tooltip ──
  nlCreateHint: '可在对话中用自然语言创建定时任务',

  // ── Filters ──
  searchPlaceholder: '搜索任务名称或描述…',
  statusAll: '全部状态',

  // ── Card / detail labels ──
  empty: '暂无定时任务',
  runCount: '执行次数',
  nextRun: '下次执行',
  runNow: '立即执行',
  description: '描述',
  timezone: '时区',
  unlimited: '无限',
  taskContent: '任务内容',
  kbId: '知识库 ID',
  skillId: '技能 ID',
  lastRun: '最后执行',
  jobId: '任务 ID',
  logs: '日志',
  pause: '暂停',
  confirmDelete: '确定删除该定时任务？',
  noRunRecords: '暂无执行记录',
  errorPrefix: '错误：',

  // ── Create / edit form ──
  jobName: '任务名称',
  jobNamePlaceholder: '例如：每日晨报',
  cronExprPlaceholder: '例如：0 9 * * *',
  descriptionPlaceholder: '可选描述',
  maxRuns: '最大执行次数',
  maxRunsPlaceholder: '留空表示无限次',
  taskContentPlaceholder: '用自然语言描述要执行的任务，例如：总结昨日上传的文档并生成 CSV 报表',
  optional: '可选',

  // ── Status labels ──
  status: {
    all: '全部状态',
    scheduled: '已计划',
    running: '执行中',
    paused: '已暂停',
    completed: '已完成',
    failed: '已失败',
  },
  statusLabel: {
    failed: '失败',
  },

  // ── Messages ──
  loadFailed: '加载失败',
  updated: '定时任务已更新',
  created: '定时任务已创建',
  saveFailed: '保存失败',
  deleted: '定时任务已删除',
  deleteFailed: '删除失败',
  statusSwitched: '状态已切换',
  switchFailed: '切换失败',
  execComplete: '执行完成',
  execCompleteWith: '执行完成：{result}',
  execFailed: '执行失败',
  loadLogFailed: '加载日志失败',
}

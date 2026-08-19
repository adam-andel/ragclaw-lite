// Copyright 2026 徐松夏（Xu Songxia）
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
export default {
  // ── Page / titles ──
  title: 'Scheduled Tasks',
  detailTitle: 'Scheduled Task Details',
  editJobTitle: 'Edit Scheduled Task',
  createJobTitle: 'New Scheduled Task',
  createNewJob: 'New Scheduled Task',
  execLog: 'Execution Log',

  // ── Header tooltip ──
  nlCreateHint: 'You can create scheduled tasks in natural language within a conversation',

  // ── Filters ──
  searchPlaceholder: 'Search task name or description…',
  statusAll: 'All Statuses',

  // ── Card / detail labels ──
  empty: 'No scheduled tasks yet',
  runCount: 'Run Count',
  nextRun: 'Next Run',
  runNow: 'Run Now',
  description: 'Description',
  cronExpr: 'Execution Time',
  timezone: 'Timezone',
  unlimited: 'Unlimited',
  taskContent: 'Task Content',
  kbId: 'Knowledge Base ID',
  skillId: 'Skill ID',
  workspaceDir: 'Workspace Directory',
  lastRun: 'Last Run',
  jobId: 'Task ID',
  logs: 'Logs',
  pause: 'Pause',
  confirmDelete: 'Delete this scheduled task?',
  noRunRecords: 'No execution records yet',
  expand: 'Expand',
  collapse: 'Collapse',
  errorPrefix: 'Error: ',

  // ── Create / edit form ──
  jobName: 'Task Name',
  jobNamePlaceholder: 'e.g. Daily Morning Report',
  cronExprPlaceholder: 'e.g. 0 9 * * *',
  descriptionPlaceholder: 'Optional description',
  maxRuns: 'Max Runs',
  maxRunsPlaceholder: 'Leave empty for unlimited runs',
  taskContentPlaceholder: 'Describe the task to run in natural language, e.g. summarize yesterday’s uploaded documents and generate a CSV report',
  optional: 'Optional',

  // ── Status labels ──
  status: {
    all: 'All Statuses',
    scheduled: 'Scheduled',
    running: 'Running',
    paused: 'Paused',
    completed: 'Completed',
    failed: 'Failed',
  },
  statusLabel: {
    failed: 'Failed',
  },

  // ── Messages ──
  loadFailed: 'Failed to load',
  updated: 'Scheduled task updated',
  created: 'Scheduled task created',
  saveFailed: 'Failed to save',
  deleted: 'Scheduled task deleted',
  deleteFailed: 'Failed to delete',
  statusSwitched: 'Status switched',
  switchFailed: 'Failed to switch',
  execComplete: 'Execution complete',
  execCompleteWith: 'Execution complete: {result}',
  execFailed: 'Execution failed',
  loadLogFailed: 'Failed to load log',
  reset: 'Reset',
  resetConfirm: 'Reset this task? Its run count will be cleared and it will be rescheduled.',
  resetSuccess: 'Task reset and rescheduled',
  resetFailed: 'Failed to reset',
}

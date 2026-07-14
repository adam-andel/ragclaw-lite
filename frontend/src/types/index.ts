// API type definitions

// ---- Document ----
export interface DocumentItem {
  id: string
  kb_id?: string | null          // legacy, may be null in new model
  filename: string
  file_type: string
  file_size: number
  status: 'pending' | 'uploaded' | 'parsing' | 'chunking' | 'embedding' | 'completed' | 'failed' | 'skipped'
  error_message?: string | null
  chunk_count: number
  progress: number               // 0-100 processing progress
  owner_id?: string | null
  kb_ids: string[]               // which KBs this doc belongs to (m2m)
  created_at: string
  updated_at?: string | null
}

export interface DocumentListResponse {
  items: DocumentItem[]
  total: number
  page: number
  size: number
}

export interface DocumentStatusResponse {
  id: string
  status: string
  error_message?: string | null
  chunk_count: number
  progress: number
}

export interface ChunkItem {
  id: string
  doc_id: string
  chunk_index: number
  content: string
  token_count: number
  heading?: string
  page?: number
}

export interface ChunkListResponse {
  items: ChunkItem[]
  total: number
  page: number
  size: number
}

// ---- Knowledge Base ----
export interface KnowledgeBase {
  id: string
  name: string
  description?: string
  prompt?: string
  doc_count: number
  vector_count: number
  created_at: string
  updated_at: string
}

export interface KBCreatePayload {
  name: string
  description?: string
  prompt?: string
}

export interface KBUpdatePayload {
  name?: string
  description?: string
  prompt?: string
}

export interface DocKBLinkResponse {
  added: number
  skipped: number
}

// ---- Chat ----
export interface ChatRequest {
  query: string
  kb_id: string
  conversation_id?: string
  skill_id?: string
  skip_cache?: boolean
}

export interface AgentStep {
  stage: string
  message: string
  skill?: string
  tool?: string
  detail?: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  created_at: string
  agentSteps?: AgentStep[]
  _pending?: boolean
  status?: string | null
  token_count?: number | null  // LLM prompt tokens of this turn (assistant only)
}

export interface Citation {
  doc_id: string
  doc_name: string
  chunk_index?: number
  heading?: string
  page?: number
  score: number
}

export interface Conversation {
  id: string
  title: string
  kb_id?: string
  created_at: string
  updated_at: string
  message_count?: number
}

// ---- Retrieval ----
export interface SearchResult {
  chunk_id: string
  doc_name: string
  heading?: string
  page?: number
  content: string
  vector_score: number
  bm25_score: number
  fusion_score: number
}

// ---- Stats ----
export interface SystemStats {
  document_count: number
  chunk_count: number
  conversation_count: number
  message_count: number
  cache_hit_rate: number
  today_token_cost: number
  hot_questions: { question: string; count: number }[]
  recent_conversations: Conversation[]
}

// ---- SSE Events ----
export type SSEEvent =
  | { type: 'queue'; position: number }
  | { type: 'token'; content: string }
  | { type: 'citation'; citation: Citation }
  | { type: 'error'; message: string }
  | { type: 'agent_step'; stage: string; message: string; skill?: string; tool?: string; detail?: string }
  | {
      type: 'done'
      conversation_id: string
      message_id: string
      cache_hit: boolean
      ttft_ms: number
      retrieval_ms: number
      llm_ms: number
      prompt_tokens?: number
      stopped?: boolean
    }
  | {
      type: 'need_user_input'
      message: string
      conv_id: string
      kind: 'skill_switch' | 'tool_round'
      message_id: string
    }

// ---- SKILL (folder-based) ----
export interface Skill {
  id: string
  tenant_id?: string | null
  folder_name: string
  name: string
  description?: string | null
  is_active: boolean
  created_at: string
  updated_at: string
  mcp_servers: string[]
  skill_md_content?: string | null
}

export interface SkillCreatePayload {
  name: string
  description?: string
  mcp_servers?: string[]
  is_active?: boolean
  body?: string
}

export interface SkillUpdatePayload {
  content: string
}

export interface SkillListResponse {
  items: Skill[]
  total: number
  page: number
  size: number
}

export interface ResourceFileInfo {
  name: string
  path: string
  size: number
}

export interface ResourceListResponse {
  scripts: ResourceFileInfo[]
  data: ResourceFileInfo[]
  references: ResourceFileInfo[]
  _root: ResourceFileInfo[]
}

export interface ResourceUploadResponse {
  path: string
  size: number
}

export interface SyncResponse {
  added: number
  updated: number
  deactivated: number
}

// ---- MCP Server ----
export interface MCPServer {
  id: string
  tenant_id?: string | null
  name: string
  transport_type: 'http' | 'stdio'
  endpoint?: string | null
  command?: string | null
  args_json?: string | null
  env_json?: string | null
  timeout_seconds: number
  is_active: boolean
  created_at: string
}

export interface MCPServerCreatePayload {
  name: string
  transport_type?: 'http' | 'stdio'
  endpoint?: string
  command?: string
  args_json?: string
  env_json?: string
  timeout_seconds?: number
  is_active?: boolean
}

export interface MCPServerUpdatePayload {
  name?: string
  transport_type?: 'http' | 'stdio'
  endpoint?: string
  command?: string
  args_json?: string
  env_json?: string
  timeout_seconds?: number
  is_active?: boolean
}

export interface MCPServerListResponse {
  items: MCPServer[]
  total: number
  page: number
  size: number
}

export interface MCPServerTestResult {
  ok: boolean
  message?: string
  error?: string
  tools?: { name: string; description: string }[]
}

// ---- Parser Plugin ----
export interface PluginInfo {
  name: string
  display_name: string
  description: string
  category: string
  extensions: string[]
  version: string
  enabled: boolean
  disabled_by: string | null
  disabled_at: string | null
  reason: string | null
}

export interface PluginListResponse {
  items: PluginInfo[]
  total: number
}

export interface PluginDisablePayload {
  reason?: string
}

// ---- Cron Jobs ----
export interface CronJob {
  id: string
  tenant_id?: string | null
  user_id?: string | null
  name: string
  description?: string | null
  cron_expr: string
  timezone: string
  max_runs?: number | null
  run_count: number
  task_content: string
  kb_id?: string | null
  skill_id?: string | null
  status: 'scheduled' | 'running' | 'paused' | 'completed' | 'failed'
  next_run_at?: string | null
  last_run_at?: string | null
  last_result?: string | null
  last_error?: string | null
  created_at: string
  updated_at?: string | null
}

export interface CronJobCreatePayload {
  name: string
  description?: string
  cron_expr: string
  timezone?: string
  max_runs?: number | null
  task_content: string
  kb_id?: string | null
  skill_id?: string | null
}

export interface CronJobUpdatePayload {
  name?: string
  description?: string
  cron_expr?: string
  timezone?: string
  max_runs?: number | null
  task_content?: string
  kb_id?: string | null
  skill_id?: string | null
}

export interface CronJobListResponse {
  items: CronJob[]
  total: number
  page: number
  size: number
}

export interface CronJobRun {
  id: string
  cron_job_id: string
  started_at?: string | null
  finished_at?: string | null
  status: string
  output?: string | null
  result_json?: string | null
  error?: string | null
}

export interface CronJobRunListResponse {
  items: CronJobRun[]
  total: number
  page: number
  size: number
}

// ---- Notifications ----
export interface NotificationItem {
  id: string
  user_id: string
  tenant_id?: string | null
  title: string
  content?: string | null
  type: 'cron_job' | 'system'
  link?: string | null
  read: boolean
  read_at?: string | null
  created_at: string
}

export interface NotificationListResponse {
  items: NotificationItem[]
  total: number
  page: number
  size: number
  unread_count: number
}

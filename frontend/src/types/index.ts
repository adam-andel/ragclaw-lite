// API 类型定义

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

// ---- Knowledge Base ----
export interface KnowledgeBase {
  id: string
  name: string
  description?: string
  doc_count: number
  vector_count: number
  created_at: string
  updated_at: string
}

export interface KBCreatePayload {
  name: string
  description?: string
}

export interface KBUpdatePayload {
  name?: string
  description?: string
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
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  created_at: string
}

export interface Citation {
  doc_id: string
  doc_name: string
  chunk_index: number
  heading?: string
  page?: number
  content_snippet: string
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
  | { type: 'token'; content: string }
  | { type: 'citation'; citation: Citation }
  | { type: 'error'; message: string }
  | { type: 'done'; conversation_id: string; message_id: string }

// ---- SKILL ----
export interface Skill {
  id: string
  tenant_id?: string | null
  name: string
  description?: string | null
  system_prompt: string
  is_active: boolean
  created_by?: string | null
  created_at: string
  updated_at: string
  tools: SkillToolInfo[]
}

export interface SkillToolInfo {
  id: string
  tool_name: string
  mcp_server_id: string
  mcp_server_name: string
}

export interface SkillCreatePayload {
  name: string
  description?: string
  system_prompt?: string
  is_active?: boolean
}

export interface SkillUpdatePayload {
  name?: string
  description?: string
  system_prompt?: string
  is_active?: boolean
}

export interface SkillToolBindPayload {
  tool_name: string
  mcp_server_id: string
  config_json?: string
}

export interface SkillToolBindResult {
  id: string
  skill_id: string
  tool_name: string
  mcp_server_id: string
}

export interface SkillListResponse {
  items: Skill[]
  total: number
  page: number
  size: number
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

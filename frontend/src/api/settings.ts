import client from './client'

export interface LLMConfig {
  llm_provider: string
  llm_model: string
  llm_api_key: string         // masked, or empty if not configured
  llm_base_url: string
  llm_temperature: number
  llm_max_tokens: number
  agent_max_tokens: number   // Agent tool-decision node specific cap (independent of llm_max_tokens)
  llm_context_window: number // max context window (tokens) of the configured model
  llm_concurrency: number
  embedding_model: string
  embedding_api_key: string   // masked, or empty if not configured
  llm_system_prompt: string       // Chinese system prompt (default when prompt_language = 'zh')
  llm_system_prompt_en: string    // English system prompt (used when prompt_language = 'en')
  prompt_language: string         // Agent-graph prompt language: 'zh' | 'en'
  server_host: string
  server_port: number
  cache_ttl_seconds: number   // cache TTL in seconds (default 3600 = 60 min)
  is_configured: boolean       // whether LLM API key has been set
  api_key_source?: 'env' | 'stored'  // where the effective API key comes from
}

export interface LLMConfigUpdate {
  llm_provider?: string
  llm_model?: string
  llm_api_key?: string
  llm_base_url?: string
  llm_temperature?: number
  llm_max_tokens?: number
  agent_max_tokens?: number
  llm_context_window?: number
  llm_concurrency?: number
  embedding_model?: string
  embedding_api_key?: string
  llm_system_prompt?: string
  llm_system_prompt_en?: string
  prompt_language?: string
  server_host?: string
  server_port?: number
  cache_ttl_seconds?: number
}

export async function getLLMConfig(): Promise<LLMConfig> {
  const res = await client.get('/config/llm')
  return res.data
}

export async function updateLLMConfig(data: LLMConfigUpdate): Promise<{ message: string; config: LLMConfig }> {
  const res = await client.put('/config/llm', data)
  return res.data
}

export async function testLLMConnection(query?: string): Promise<{ ok: boolean; reply?: string; error?: string; model?: string }> {
  const res = await client.post('/config/llm/test', { query: query || 'Hello, respond with OK only.' })
  return res.data
}

export interface SandboxNetworkConfig {
  sandbox_network_mode: 'deny' | 'allow' | 'allowlist'
  sandbox_allow_domains: string
  sandbox_allow_methods: string
}

export async function getSandboxNetwork(): Promise<SandboxNetworkConfig> {
  const res = await client.get('/config/sandbox-network')
  return res.data
}

export async function updateSandboxNetwork(data: Partial<SandboxNetworkConfig>): Promise<{ message: string; config: SandboxNetworkConfig; mcp_pushed: boolean }> {
  const res = await client.put('/config/sandbox-network', data)
  return res.data
}

// ── REPL MCP identity secret (HMAC) ──
export interface ReplAuthConfig {
  repl_auth_secret: string
}

export async function getReplAuth(): Promise<ReplAuthConfig> {
  const res = await client.get('/config/repl-auth')
  return res.data
}

export async function updateReplAuth(secret: string): Promise<{ message: string; repl_auth_secret: string; mcp_pushed: boolean }> {
  const res = await client.put('/config/repl-auth', { repl_auth_secret: secret })
  return res.data
}

export async function regenerateReplAuth(): Promise<{ message: string; repl_auth_secret: string; mcp_pushed: boolean }> {
  const res = await client.post('/config/repl-auth/regenerate')
  return res.data
}

// ── Embedding model (on-demand download) ──
export interface EmbeddingModelOption {
  id: string
  label: string
  dimension: number
  size: string
}

export interface EmbeddingModelStatus {
  status: 'idle' | 'downloading' | 'paused' | 'completed' | 'failed' | 'cancelled'
  progress: number
  message: string
  error: string
  model: string
  installed: boolean
  configured_model: string
  installed_models: string[]
  options: EmbeddingModelOption[]
}

export async function getEmbeddingModelStatus(): Promise<EmbeddingModelStatus> {
  const res = await client.get('/embedding-model/status')
  return res.data
}

export async function downloadEmbeddingModel(model?: string): Promise<{ started: boolean; reason?: string; model: string }> {
  const res = await client.post('/embedding-model/download', model ? { model } : {})
  return res.data
}

export async function pauseEmbeddingDownload(): Promise<{ paused: boolean }> {
  const res = await client.post('/embedding-model/pause')
  return res.data
}

export async function resumeEmbeddingDownload(): Promise<{ resumed: boolean }> {
  const res = await client.post('/embedding-model/resume')
  return res.data
}

export async function cancelEmbeddingDownload(): Promise<{ cancelled: boolean }> {
  const res = await client.post('/embedding-model/cancel')
  return res.data
}

export async function deleteEmbeddingModel(model?: string): Promise<{ deleted: boolean; model: string }> {
  const res = await client.delete('/embedding-model', { data: model ? { model } : {} })
  return res.data
}

export interface SwitchEmbeddingResult {
  switched: boolean
  model: string
  installed: boolean
  cleared_vectors: boolean
  reindex_started: boolean
}

export async function switchEmbeddingModel(model: string, force = false): Promise<SwitchEmbeddingResult> {
  const res = await client.post('/embedding-model/switch', { model, force })
  return res.data
}

// ── Dry-run dimension check (no mutation) ──
// Returns 200 when switching is safe; throws HTTP 409 (detail carries
// existing/new dimension + vector count) when the dimensions are incompatible.
export async function checkEmbeddingDimension(model: string): Promise<{
  conflict: boolean
  existing_dim: number | null
  new_dim: number | null
  vector_count: number
}> {
  const res = await client.post('/embedding-model/check', { model })
  return res.data
}

// ── Re-index all documents against the active embedding model ──
export interface ReindexStatus {
  status: 'idle' | 'running' | 'completed' | 'failed'
  progress: number
  message: string
  error: string
  current: number
  total: number
}

export async function getReindexStatus(): Promise<ReindexStatus> {
  const res = await client.get('/documents/reindex/status')
  return res.data
}

export async function startReindex(): Promise<{ started: boolean; reason?: string }> {
  const res = await client.post('/documents/reindex')
  return res.data
}

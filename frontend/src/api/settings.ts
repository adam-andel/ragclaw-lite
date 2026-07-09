import client from './client'

export interface LLMConfig {
  llm_provider: string
  llm_model: string
  llm_api_key: string         // masked, or empty if not configured
  llm_base_url: string
  llm_temperature: number
  llm_max_tokens: number
  agent_max_tokens: number   // Agent 工具决策节点专用上限（独立于 llm_max_tokens）
  llm_concurrency: number
  embedding_model: string
  embedding_api_key: string   // masked, or empty if not configured
  llm_system_prompt: string
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
  llm_concurrency?: number
  embedding_model?: string
  embedding_api_key?: string
  llm_system_prompt?: string
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

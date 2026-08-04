export default {
  loginExpired: '登录已过期，请重新登录',
  loginExpiredShort: '登录已过期',
  networkError: '网络错误',
  thinking: '💭 思考过程',

  // Backend error CODE -> localized message (generic, all surfaces: chat, settings, …).
  // Raw exception text that is not a known code is shown as-is. Reuses the same
  // code vocabulary as documents.docErrorCodes but with context-neutral wording.
  backendErrorCodes: {
    EMBED_MODEL_NOT_INSTALLED: 'Embedding 模型未安装，无法执行向量化与语义检索。请前往「系统设置 → Embedding 模型」安装模型。',
    LLM_BUDGET_EXCEEDED: '本次问答的 LLM 调用超出时间预算，已中止以避免长时间无响应。上游模型可能响应过慢，请稍后重试或调整模型配置。',
    CONVERSATION_STATE_NOT_RECOVERABLE: '没有可恢复的对话状态，请重新发起提问。',
    CONVERSATION_BUSY: '该对话有正在等待确认的暂停回合，请先处理后再修改上下文。',
    NOTHING_TO_COMPACT: '没有可压缩的对话记录，全部消息都已折叠进摘要。',
    SUMMARY_LLM_FAILED: '摘要生成失败，未做任何改动，请稍后重试。',
    CRON_JOB_NOT_FOUND: '定时任务不存在',
    CRON_JOB_FORBIDDEN: '无权访问该定时任务',
    CRON_JOB_RUNNING: '任务正在执行中，请等待当前执行完成',
    CRON_JOB_COMPLETED: '任务已完成，请先重置状态后再执行',
    CRON_JOB_INVALID_STATE: '当前状态无法切换',
    CRON_JOB_INVALID_STATUS: '无效的定时任务状态',
    CRON_EXEC_FAILED: '执行失败',

    CONFIG_API_KEY_EMPTY: 'API Key 不能为空',
    CONFIG_API_KEY_NOT_CONFIGURED: '尚未配置 API Key，请先在设置页面录入',
    CONFIG_NO_FIELDS: '没有提供任何可更新的字段',
    CONFIG_REPL_SECRET_EMPTY: 'REPL_AUTH_SECRET 不能为空',
    CONFIG_REPL_SECRET_SHORT: 'REPL_AUTH_SECRET 至少 16 个字符',

    CONFIG_UPDATED: '配置已更新，立即生效。',
    CONFIG_TEMPERATURE_RANGE: 'temperature 必须在 0-2 之间。',
    CONFIG_MAX_TOKENS_RANGE: 'max_tokens 必须在 128-131072 之间。',
    CONFIG_CONTEXT_WINDOW_RANGE: '上下文窗口必须在 1-10,000,000 之间。',
    CONFIG_CONCURRENCY_RANGE: '并发数必须在 1-50 之间。',
    CONFIG_AGENT_ROUND_QUOTA_RANGE: '工具调用轮次配额必须在 0-200 之间（0 表示不限轮数）。',
    CONFIG_SANDBOX_NETWORK_MODE: 'network mode 必须是 deny / allow / allowlist 之一。',
    SANDBOX_POLICY_UPDATED: '沙盒策略已更新，立即生效。',
    REPL_AUTH_SECRET_REGENERATED: '已生成新的 REPL_AUTH_SECRET，立即生效。',

    EMBEDDING_MODEL_UNKNOWN: '未知模型：{detail}',
    EMBEDDING_MODEL_DIM_UNKNOWN: '无法确定模型维度，请先安装 {detail} 后再切换',
    EMBEDDING_MODEL_NOT_INSTALLED_SWITCH: '模型 {detail} 尚未安装，请先下载安装后再切换。',

    MCP_SERVER_NOT_FOUND: 'MCP 服务不存在',
    MCP_SERVER_BUILTIN_NO_EDIT: '内置 MCP 服务不可修改',
    MCP_SERVER_BUILTIN_NO_DELETE: '内置 MCP 服务不可删除',
    MCP_SERVER_HTTP_NO_ENDPOINT: 'HTTP 传输缺少 endpoint',

    USER_CREATE_ROLE: '普通管理员只能创建普通用户',
    USER_NAME_EXISTS: '用户名已存在',
    SETUP_ALREADY_COMPLETE: '系统初始化已完成，注册已关闭。请使用已有账号登录。',
    USER_UID_POOL_EXHAUSTED: '无法为新建用户分配沙盒隔离 UID：UID 池可能已耗尽，请扩大 REPL_UID_RANGE_MAX。',
    USER_NOT_FOUND: '用户不存在',
    USER_NO_MANAGE_PERM: '无权管理该用户',
    USER_ROLE_PERMISSION: '普通管理员只能设置普通用户角色',
    USER_CANNOT_DELETE_SELF: '不能删除自己',
    USER_NO_DELETE_PERM: '无权删除该用户',
    USER_SANDBOX_CLEANUP_FAILED: '无法清理用户 {detail} 的沙盒目录（mcp-repl 可能不可用），用户未删除。请排查 mcp-repl 服务后重试。',

    PLUGIN_NOT_FOUND: '插件不存在: {detail}',

    WORKSPACE_SANDBOX_NOT_INIT: '用户沙箱未初始化',
    WORKSPACE_REPL_AUTH_MISSING: 'REPL 认证未配置',
    WORKSPACE_MCP_UNAVAILABLE: 'MCP REPL 服务不可用',
    WORKSPACE_PROXY_ERROR: '工作空间代理错误: {detail}',
    WORKSPACE_DOWNLOAD_ERROR: '下载代理错误: {detail}',
    WORKSPACE_FILE_NOT_FOUND: '文件不存在',
    WORKSPACE_MCP_STATUS: 'MCP 错误 {detail}',
    WORKSPACE_MISSING_PATHS: '缺少要下载的路径',
  },
}

export default {
  loginExpired: 'Session expired, please log in again',
  loginExpiredShort: 'Session expired',
  networkError: 'Network error',
  thinking: '💭 Thinking',

  // Backend error CODE -> localized message (generic, all surfaces: chat, settings, …).
  // Raw exception text that is not a known code is shown as-is. Reuses the same
  // code vocabulary as documents.docErrorCodes but with context-neutral wording.
  backendErrorCodes: {
    EMBED_MODEL_NOT_INSTALLED: 'Embedding model not installed — vectorization and semantic retrieval are unavailable. Install a model in "Settings → Embedding Model".',
    LLM_BUDGET_EXCEEDED: 'The LLM call budget for this conversation was exceeded and the request was aborted to avoid a long hang. The upstream model may be responding too slowly — retry later or adjust the model configuration.',
    CONVERSATION_STATE_NOT_RECOVERABLE: 'No recoverable conversation state found. Please start a new question.',
    CONVERSATION_BUSY: 'This conversation has a paused turn awaiting your confirmation. Resolve it before editing the context.',
    NOTHING_TO_COMPACT: 'Nothing left to compact — every message is already folded into the summary.',
    SUMMARY_LLM_FAILED: 'Summarization failed; nothing was changed. Please try again.',
    CRON_JOB_NOT_FOUND: 'Cron job not found',
    CRON_JOB_FORBIDDEN: 'You do not have permission to access this cron job',
    CRON_JOB_RUNNING: 'This task is currently running. Please wait for it to finish.',
    CRON_JOB_COMPLETED: 'This task is completed. Reset its status before running it again.',
    CRON_JOB_INVALID_STATE: 'Cannot switch from the current status.',
    CRON_JOB_INVALID_STATUS: 'Invalid cron job status.',
    CRON_EXEC_FAILED: 'Execution failed.',

    CONFIG_API_KEY_EMPTY: 'API Key cannot be empty',
    CONFIG_API_KEY_NOT_CONFIGURED: 'API Key is not configured yet. Please add it in Settings first.',
    CONFIG_NO_FIELDS: 'No updatable fields were provided',
    CONFIG_REPL_SECRET_EMPTY: 'REPL_AUTH_SECRET cannot be empty',
    CONFIG_REPL_SECRET_SHORT: 'REPL_AUTH_SECRET must be at least 16 characters',

    EMBEDDING_MODEL_UNKNOWN: 'Unknown embedding model: {detail}',
    EMBEDDING_MODEL_DIM_UNKNOWN: 'Unable to determine model dimensions. Please install {detail} before switching.',
    EMBEDDING_MODEL_NOT_INSTALLED_SWITCH: 'Model {detail} is not installed yet. Please download and install it before switching.',

    MCP_SERVER_NOT_FOUND: 'MCP server not found',
    MCP_SERVER_BUILTIN_NO_EDIT: 'Built-in MCP servers cannot be modified',
    MCP_SERVER_BUILTIN_NO_DELETE: 'Built-in MCP servers cannot be deleted',
    MCP_SERVER_HTTP_NO_ENDPOINT: 'HTTP transport requires an endpoint',

    USER_CREATE_ROLE: 'Moderators can only create regular users',
    USER_NAME_EXISTS: 'Username already exists',
    USER_UID_POOL_EXHAUSTED: 'Unable to allocate a sandbox UID for the new user: the UID pool may be exhausted. Please increase REPL_UID_RANGE_MAX.',
    USER_NOT_FOUND: 'User not found',
    USER_NO_MANAGE_PERM: 'You do not have permission to manage this user',
    USER_ROLE_PERMISSION: 'Moderators can only assign the regular user role',
    USER_CANNOT_DELETE_SELF: 'You cannot delete yourself',
    USER_NO_DELETE_PERM: 'You do not have permission to delete this user',
    USER_SANDBOX_CLEANUP_FAILED: 'Failed to clean up the user sandbox directory (mcp-repl may be unavailable); the user was not deleted. Please check the mcp-repl service and retry. Original error: {detail}',

    PLUGIN_NOT_FOUND: 'Plugin not found: {detail}',

    WORKSPACE_SANDBOX_NOT_INIT: 'User sandbox is not initialized',
    WORKSPACE_REPL_AUTH_MISSING: 'REPL authentication is not configured',
    WORKSPACE_MCP_UNAVAILABLE: 'MCP REPL service is unavailable',
    WORKSPACE_PROXY_ERROR: 'Workspace proxy error: {detail}',
    WORKSPACE_DOWNLOAD_ERROR: 'Download proxy error: {detail}',
    WORKSPACE_FILE_NOT_FOUND: 'File not found',
    WORKSPACE_MCP_STATUS: 'MCP error {detail}',
    WORKSPACE_MISSING_PATHS: 'Missing paths to download',
  },
}

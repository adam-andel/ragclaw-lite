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
  },
}

export default {
  // ── Page / titles ──
  title: '技能管理',
  createModalTitle: '在线创建技能',
  detailTitle: '技能详情',
  editSkillMd: '编辑 SKILL.md',

  // ── Header actions ──
  sync: '同步',
  upload: '上传',
  uploadFolder: '上传文件夹',
  uploadZip: '上传ZIP',
  uploadModalTitle: '上传技能',
  createOnline: '在线创建',

  // ── 上传弹窗（自适应拖拽区） ──
  dragDropTitle: '拖拽技能文件夹到此处',
  dragDropHint: '或点击选择文件夹 — 需包含 SKILL.md',
  dragDropZipTitle: '拖拽 .zip 文件到此处',
  dragDropZipHint: '或点击选择 .zip 压缩包 — 保留文件夹结构',

  // ── Filters ──
  searchPlaceholder: '搜索技能名称或描述…',
  statusAll: '全部状态',

  // ── Empty / placeholders / labels ──
  empty: '暂无技能，请上传或在线创建',
  noDescription: '暂无描述',
  mcpService: 'MCP服务',
  none: '无',
  updated: '更新',
  namePlaceholder: '如：IT运维助手',
  description: '描述',
  descriptionPlaceholder: '≤250字符，给LLM路由看的技能描述',
  mcpServicePlaceholder: '选择该技能可用的MCP服务（可选）',
  skillBody: 'SKILL正文',
  skillBodyPlaceholder: 'SKILL.md 的 Markdown 正文（front matter 自动生成）',
  skillBodyTemplate: '---\nname: ...\ndescription: ...\nmcp_servers:\n  - ...\n---\n\n# 正文',
  folder: '文件夹',
  skillId: '技能 ID',
  editSkillMdHint: '直接编辑 SKILL.md 全文。YAML front matter 中的 name/description/mcp_servers 会同步到数据库索引，is_active 通过上方开关管理。',

  // ── Detail / action buttons ──
  reupload: '重新上传',
  reuploadZip: '重新上传ZIP',
  selectZipFile: '选择 ZIP 文件',
  confirmDeleteSkill: '确认删除此技能？文件夹和DB记录都会被删除。',

  // ── API KEY (secret-zero 代理注入) ──
  apiKey: 'API KEY（代理注入）',
  apiKeyActive: '已启用代理注入',
  apiKeyVanilla: '未配置（原生模式）',
  apiKeyPlaceholder: '填写该技能的 API KEY…',
  apiKeySave: '保存',
  apiKeyClear: '清除',
  apiKeySaved: 'API KEY 已保存，代理注入已启用',
  apiKeyCleared: 'API KEY 已清除，回退原生模式',

  // ── Messages ──
  loadFailed: '加载失败',
  created: '技能已创建',
  createFailed: '创建失败',
  loadDetailFailed: '加载技能详情失败',
  skillMdSaved: 'SKILL.md 已保存',
  saveFailed: '保存失败',
  deleted: '技能已删除',
  deleteFailed: '删除失败',
  folderMustContainSkillMd: '上传的文件夹必须包含 SKILL.md',
  folderUploadSuccess: '技能文件夹上传成功',
  uploadFailed: '上传失败',
  pleaseUploadZip: '请上传 .zip 文件',
  zipUploadSuccess: 'ZIP 上传成功',
  folderReuploaded: '技能文件夹已重新上传并替换',
  reuploadFailed: '重新上传失败',
  zipReuploaded: '技能 ZIP 已重新上传并替换',
  skillDisabled: '技能已禁用',
  skillEnabled: '技能已启用',
  operationFailed: '操作失败',
  syncComplete: '同步完成：新增 {added}，更新 {updated}，停用 {deactivated}',
  syncFailed: '同步失败',
}

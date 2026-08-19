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
﻿export default {
  // Generic UI labels
  newKb: '新建知识库',
  editKb: '编辑知识库',
  uploadDoc: '上传文档',
  uploadFile: '上传文件',
  linkKb: '关联知识库',
  currentKb: '当前知识库',
  startChat: '发起对话',
  shareUsers: '共享用户',
  shareUsersTooltip: '选择可以使用这个知识库的用户',
  addDocs: '添加文档',
  deleteKbTooltip: '删除知识库不会删除关联文档',
  noDocsUpload: '暂无文档，请上传',
  chunkPreview: '分块预览',
  searchChunkContent: '搜索分块内容…',
  noChunkData: '暂无分块数据',
  noMatchingChunks: '无匹配的分块',
  noMatchingKb: '未找到匹配的知识库',
  docDetail: '文档详情',
  fileName: '文件名',
  fileType: '文件类型',
  fileSize: '文件大小',
  errorMessage: '错误信息',
  chunkNumber: '分块数',
  linkedKbsLabel: '关联知识库',
  notLinkedKb: '未关联知识库',
  docId: '文档 ID',
  downloadOriginal: '下载原件',
  notLinked: '不关联',
  dontUploadToKb: '不上传至知识库',
  showAllDocs: '显示所有文档',
  noSharedUsers: '暂无共享用户',
  confirmUnshareUser: '确定取消共享给此用户吗',
  addMoreUsers: '添加更多用户',
  searchUser: '搜索用户…',
  searchKbName: '搜索知识库名称…',
  noUsersToAdd: '暂无可添加的用户',
  selectDocsToAdd: '选择文档加入知识库',
  noAvailableDocs: '还没有可添加的文档',
  goToUploadDocs: '前往文档管理页上传文档',
  uploadMoreDocs: '上传更多文档',
  addToKb: '加入知识库',

  // Form placeholders / hints
  kbNamePlaceholder: '知识库名称',
  descOptional: '描述（可选）',
  promptHint: '提示词（可选，让大模型更了解本知识库或团队需求。注意：每次修改都会使得修改后的第一次对话LLM缓存命中率大幅下降。）',

  // Upload zone
  dragDropHint: '点击或拖拽文件到此处上传',
  clearCompleted: '清空已完成',
  uploading: '上传中…',
  startUpload: '开始上传',
  startUploadCount: '开始上传 ({count})',
  pause: '全部暂停',
  resume: '继续上传',
  knowledgeBase: '知识库',

  // Filters / options
  allTypes: '全部类型',
  loadingFormats: '加载支持格式中…，单文件最大 50MB',
  supportedFormats: '支持 {formats}，单文件最大 50MB',
  allStatus: '全部状态',
  unlinked: '未关联',

  // Dynamic-count labels
  fileCount: '{count} 个文件',
  chunkCount: '{count} 分块',
  linkedKbs: '关联{count} 个知识库',
  chunkTotal: '{count} 个分块',
  countMeta: '{docs} 文档 · {vectors} 向量',
  kbDocMeta: '{count} 文档',
  kbVectorMeta: '{count} 分片',
  selectedPrefix: '已选 ',
  totalDocsSuffix: ' / 共 {count} 个文档',
  selectAll: '全选',
  create: '创建',

  // Status labels (document processing)
  status: {
    waiting: '等待中',
    uploaded: '已上传',
    parsing: '解析中',
    chunking: '分块中',
    embedding: '向量化中',
    chunked: '已切片',
    completed: '已完成',
    failed: '失败',
    processing: '处理中',
  },
  // Status hover hints: meaning + what the user can do
  statusHint: {
    waiting: '已加入处理队列，等待开始解析。处理完成后即可用于检索，无需操作。',
    uploaded: '文件已上传成功，等待开始解析。无需操作，稍后会自动处理。',
    parsing: '正在从文件中提取文本。请稍候，处理完成后会自动进入下一步。',
    chunking: '正在将文本切分为片段。请稍候，处理完成后会自动进入下一步。',
    embedding: '正在生成向量嵌入。完成后即可用于问答检索，请稍候。',
    chunked: '已完成分块，但未安装 Embedding 模型。请到「系统设置 → Embedding 模型」安装模型并点击「切换并重建索引」。',
    completed: '处理完成，已可用于问答检索。可点击卡片查看分块内容或下载原文。',
    failed: '处理失败。可打开详情删除该文档，然后重新上传。',
  },

  // 后端错误码 -> 本地化文案（用于 'failed' 或 'chunked' 文档）。
  // 真正的异常原文不做本地化，原样展示。
  docErrorCodes: {
    EMBED_MODEL_NOT_INSTALLED: 'Embedding 模型未安装，文档已切片但仅支持关键词检索。请前往「系统设置 → Embedding 模型」安装模型后点击「重建索引」。',
  },

  // Upload item status tags
  upload: {
    waiting: '等待',
    uploading: '上传中',
    complete: '完成',
    failed: '失败',
    cancelled: '已取消',
  },

  // useMessage success/error/warning
  loadDocsFailed: '加载文档失败：',
  kbCreated: '知识库创建成功',
  kbUpdated: '知识库已更新',
  kbCreateFailed: '创建失败：',
  kbUpdateFailed: '更新失败：',
  kbDeleted: '知识库已删除',
  kbDeleteFailed: '删除失败：',
  shareUserAdded: '已添加共享用户',
  addShareUserFailed: '添加失败：',
  shareUserRemoved: '已移除共享用户',
  removeShareUserFailed: '移除失败：',
  linkDocsFailed: '添加失败：',
  fileTooLarge: '文件过大：{name} ({size}MB)',
  uploadComplete: '上传完成',
  docDeleted: '文档已删除',
  docDeleteFailed: '删除失败：',
  deleteSelected: '删除选中 ({count})',
  selectedCount: '已选 {count} 项',
  confirmBatchDelete: '确定删除选中的 {count} 个文档吗？此操作不可撤销。',
  batchDeleted: '已删除 {count} 个文档',
  batchUnlinkSelected: '解除关联 ({count})',
  confirmBatchUnlink: '确定解除 {count} 个选中文档与「{kb}」的关联？',
  batchUnlinked: '已解除 {count} 个文档与「{kb}」的关联',
  batchUnlinkFailed: '批量解除关联失败：',
  batchDeleteFailed: '删除失败：',
  unlinkedFromKb: '已解除与「{kb}」的关联',
  unlinkFailed: '解除关联失败：',
  downloadFailed: '下载失败：',
  unknownError: '未知错误',
  loadChunksFailed: '加载分块失败',
  docsAdded: '已添加 {added} 个文档',
  docsSkipped: '，跳过 {skipped} 个',
  uploadInterruptedByClose: '页面关闭导致上传中断',

  // Popconfirm texts
  confirmDeleteKb: '确定删除「{kb}」？文档不会被删除，仅解除关联。',
  confirmUnlinkDoc: '确定解除该文档与「{kb}」的关联？',
  confirmDeleteDoc: '确定删除文档「{filename}」？将从所有知识库中移除。',

  // 检索配置
  retrievalConfig: '检索配置',
  retrievalConfigDesc: '为知识库{name}配置检索参数，留空则使用全局默认值。',
  retrievalConfigUpdated: '检索配置更新成功',
  retrievalConfigUpdateFailed: '检索配置更新失败',
  weightConfig: '权重配置',
  vectorWeight: '向量权重',
  bm25Weight: 'BM25 权重',
  range: '范围',
  weightSumNote: '向量权重 + BM25 权重应等于 1.0。修改其中一个会自动调整另一个。',
  topKConfig: 'Top-K 配置',
  vectorTopK: '向量 Top-K',
  bm25TopK: 'BM25 Top-K',
  finalTopK: '最终 Top-K',
  thresholdConfig: '阈值配置',
  similarityThreshold: '相似度阈值',
  thresholdNote: '结果包含的最小相似度分数。值越高 = 越精确但结果越少。',
  vectorWeightHint: '向量语义搜索的权重，影响语义相似度的匹配程度',
  bm25WeightHint: 'BM25 关键词搜索的权重，影响关键词匹配的精确度',
  vectorTopKHint: '向量搜索召回的候选数量，值越大召回越多但速度越慢',
  bm25TopKHint: 'BM25 搜索召回的候选数量，值越大召回越多但速度越慢',
  finalTopKHint: '最终返回给大模型的结果数量',
  similarityThresholdHint: '过滤低于此分数的结果，值越高结果越精确但数量越少',
}
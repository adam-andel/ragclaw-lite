export default {
  // ── Page / titles ──
  title: 'Skill Management',
  createModalTitle: 'Create Skill',
  detailTitle: 'Skill Details',
  editSkillMd: 'Edit SKILL.md',

  // ── Header actions ──
  sync: 'Sync',
  syncTooltip: 'Sync skills that were added manually on disk',
  upload: 'Upload',
  uploadFolder: 'Upload Folder',
  uploadZip: 'Upload ZIP',
  uploadModalTitle: 'Upload Skill',
  reuploadModalTitle: 'Re-upload for {name}',
  reuploadHint: 'This will replace the existing content of “{name}”.',
  createOnline: 'New Skill',

  // ── Upload modal (adaptive drag & drop) ──
  dragDropTitle: 'Drag & drop a skill folder here',
  dragDropHint: 'or click to browse — the folder must contain SKILL.md',
  dragDropZipTitle: 'Drag & drop a .zip file here',
  dragDropZipHint: 'or click to select a .zip archive — folder structure is preserved',

  // ── Filters ──
  searchPlaceholder: 'Search skill name or description…',
  statusAll: 'All Statuses',

  // ── Empty / placeholders / labels ──
  empty: 'No skills yet. Upload a folder or create one online.',
  noDescription: 'No description',
  mcpService: 'MCP Service',
  none: 'None',
  updated: 'Updated',
  namePlaceholder: 'e.g. IT Ops Assistant',
  description: 'Description',
  descriptionPlaceholder: '≤250 chars, description shown to the LLM router',
  mcpServicePlaceholder: 'Select MCP services available to this skill (optional)',
  skillBody: 'SKILL.md Body',
  skillBodyPlaceholder: 'Markdown body of SKILL.md (front matter auto-generated)',
  skillBodyTemplate: '---\nname: ...\ndescription: ...\nmcp_servers:\n  - ...\n---\n\n# Body',
  folder: 'Folder',
  skillId: 'Skill ID',
  editSkillMdHint: 'Edit the full SKILL.md content directly. The name/description/mcp_servers in the YAML front matter will be synced to the database index, and is_active is managed via the switch above.',

  // ── Detail / action buttons ──
  reupload: 'Re-upload',
  reuploadZip: 'Re-upload ZIP',
  selectZipFile: 'Select ZIP File',
  confirmDeleteSkill: 'Delete this skill? Both the folder and the database record will be removed.',

  // ── API KEY (secret-zero proxy injection) ──
  apiKeyButton: 'API KEY',
  apiKeyModalTitle: 'API KEY Configuration',
  apiKeyModalHint: 'This API KEY is injected into the skill runtime via the proxy (proxy-injection mode). The key is stored securely and never echoed back.',
  apiKeyConfigStatus: 'Status',
  apiKeyActive: 'Proxy injection enabled',
  apiKeyVanilla: 'Not configured (vanilla)',
  apiKeyPlaceholder: "Enter this skill's API KEY…",
  apiKeySave: 'Save',
  apiKeyClear: 'Clear',
  apiKeySaved: 'API KEY saved — proxy injection enabled',
  apiKeyCleared: 'API KEY cleared — back to vanilla',

  // ── Messages ──
  loadFailed: 'Failed to load',
  created: 'Skill created',
  createFailed: 'Failed to create',
  loadDetailFailed: 'Failed to load skill details',
  skillMdSaved: 'SKILL.md saved',
  saveFailed: 'Failed to save',
  deleted: 'Skill deleted',
  deleteFailed: 'Failed to delete',
  folderMustContainSkillMd: 'The uploaded folder must contain SKILL.md',
  folderUploadSuccess: 'Skill folder uploaded',
  uploadFailed: 'Upload failed',
  pleaseUploadZip: 'Please upload a .zip file',
  zipUploadSuccess: 'ZIP uploaded',
  folderReuploaded: 'Skill folder re-uploaded and replaced',
  reuploadFailed: 'Re-upload failed',
  zipReuploaded: 'Skill ZIP re-uploaded and replaced',
  skillDisabled: 'Skill disabled',
  skillEnabled: 'Skill enabled',
  operationFailed: 'Operation failed',
  syncComplete: 'Sync complete: {added} added, {updated} updated, {deactivated} deactivated',
  syncFailed: 'Sync failed',
}

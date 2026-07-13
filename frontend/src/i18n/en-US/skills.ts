export default {
  // ── Page / titles ──
  title: 'Skill Management',
  createModalTitle: 'Create Skill Online',
  detailTitle: 'Skill Details',
  editSkillMd: 'Edit SKILL.md',

  // ── Header actions ──
  sync: 'Sync',
  uploadFolder: 'Upload Folder',
  uploadZip: 'Upload ZIP',
  createOnline: 'Create Online',

  // ── Filters ──
  searchPlaceholder: 'Search skill name or description…',
  statusAll: 'All Status',

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
  skillBody: 'SKILL Body',
  skillBodyPlaceholder: 'Markdown body of SKILL.md (front matter auto-generated)',
  skillBodyTemplate: '---\nname: ...\ndescription: ...\nmcp_servers:\n  - ...\n---\n\n# Body',
  folder: 'Folder',
  skillId: 'Skill ID',
  editSkillMdHint: 'Edit the full SKILL.md content directly. The name/description/mcp_servers in the YAML front matter will be synced to the database index, and is_active is managed via the switch above.',

  // ── Detail / action buttons ──
  reupload: 'Re-upload',
  reuploadZip: 'Re-upload ZIP',
  selectZipFile: 'Select ZIP File',
  confirmDeleteSkill: 'Delete this skill? Both the folder and the DB record will be removed.',

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

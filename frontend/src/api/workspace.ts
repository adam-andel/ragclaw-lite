import client from './client'

export interface WorkspaceEntry {
  name: string
  type: 'dir' | 'file'
  rel_path: string
  size: number | null
  mtime: number
  download_url: string | null
}

export interface WorkspaceListResponse {
  path: string
  entries: WorkspaceEntry[]
}

// ── Listing & download ──

export const listWorkspace = (path = '', search = '') => {
  const params: Record<string, string> = { path }
  if (search) params.search = search
  return client.get<WorkspaceListResponse>('/workspace/list', { params }).then(r => r.data)
}

export const downloadWorkspace = (path: string) =>
  client.get<Blob>('/workspace/download', {
    params: { path },
    responseType: 'blob',
  }).then(r => r.data)

// ── Create / update / rename / delete ──

export const mkdirWorkspace = (name: string) =>
  client.post('/workspace', { action: 'mkdir', name }).then(r => r.data)

export const uploadWorkspace = (name: string, contentBase64: string) =>
  client.post('/workspace', { action: 'upload', name, content: contentBase64 }).then(r => r.data)

export const renameWorkspace = (path: string, newName: string) =>
  client.post('/workspace', { action: 'rename', path, new_name: newName }).then(r => r.data)

export const deleteWorkspace = (path: string) =>
  client.delete('/workspace', { params: { path } }).then(r => r.data)

// ── Helpers ──

/** Encode a browser File as a base64 data string (strips the data: prefix). */
export const fileToBase64 = (file: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result as string
      const comma = result.indexOf(',')
      resolve(comma >= 0 ? result.slice(comma + 1) : result)
    }
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })

/** Trigger a browser download from a Blob. */
export const triggerDownload = (blob: Blob, filename: string) => {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

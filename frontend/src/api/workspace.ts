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
  truncated?: boolean
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

/** Download a file and trigger the browser "save" dialog, using the real
 *  filename from the server's Content-Disposition header. */
export const downloadAndSave = async (path: string) => {
  const res = await client.get<Blob>('/workspace/download', {
    params: { path },
    responseType: 'blob',
  })
  const disp = res.headers['content-disposition'] || ''
  const m = /filename\*=UTF-8''([^;]+)/i.exec(disp) || /filename="?([^";]+)"?/i.exec(disp)
  let filename = decodeURIComponent(m?.[1] || '')
  if (!filename) filename = path.split('/').pop() || 'download'
  triggerDownload(res.data, filename)
}

/**
 * Bundle the given files/dirs into one zip (server keeps the directory
 * structure) and trigger a single download. `root` optionally nests everything
 * under one top-level folder inside the archive. Throws if the server returns
 * an error document instead of a real zip.
 */
export const downloadZip = async (paths: string[], root = 'workspace') => {
  const res = await client.post<Blob>('/workspace/download-zip', { paths, root }, {
    responseType: 'blob',
  })
  const blob = res.data
  // The server signals an upstream error by prefixing the body with
  // "__RAGCLAW_ZIP_ERROR__" instead of returning a real zip. Detect that.
  const head = await blob.slice(0, 21).text()
  if (head.startsWith('__RAGCLAW_ZIP_ERROR__')) {
    const text = await blob.text()
    const msg = text.replace(/^__RAGCLAW_ZIP_ERROR__/, '').trim()
    throw new Error(msg || 'Pack download failed')
  }
  triggerDownload(blob, `${root}.zip`)
}

// ── Create / update / rename / delete ──

export const mkdirWorkspace = (name: string) =>
  client.post('/workspace', { action: 'mkdir', name }).then(r => r.data)

export const uploadWorkspace = (name: string, contentBase64: string) =>
  client.post('/workspace', { action: 'upload', name, content: contentBase64 }).then(r => r.data)

export const renameWorkspace = (path: string, newName: string) =>
  client.post('/workspace', { action: 'rename', path, new_name: newName }).then(r => r.data)

/** Move N files/dirs into a target directory (dest = '' means the sandbox root). */
export const moveWorkspace = (paths: string[], dest: string) =>
  client.post('/workspace', { action: 'move', paths, dest }).then(r => r.data)

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

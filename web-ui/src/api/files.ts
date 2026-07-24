import api from './client'

export interface UploadSession {
  id: string
  filename: string
  size: number
  sha256: string
  received_bytes: number
  chunk_size: number
  status: string
  created: string | null
  updated: string | null
  completed: string | null
}

export interface TransferTarget {
  id: string
  node_id: string
  status: string
  bytes_sent: number
  progress: number
  attempts: number
  error: string | null
  started: string | null
  finished: string | null
}

export interface FileTransfer {
  id: string
  upload_id: string
  filename: string
  size: number
  sha256: string
  dest_path: string
  overwrite: boolean
  status: string
  created: string | null
  started: string | null
  finished: string | null
  targets: TransferTarget[]
}

export const filesApi = {
  createUpload: (params: { filename: string; size: number; sha256: string }) =>
    api.post<UploadSession>('/files/uploads', params),

  listUploads: (status?: string) =>
    api.get<UploadSession[]>('/files/uploads', { params: { status, limit: 50 } }),

  getUpload: (uploadId: string) =>
    api.get<UploadSession>(`/files/uploads/${uploadId}`),

  uploadChunk: (
    uploadId: string, offset: number, chunkSha256: string, chunk: Blob,
  ) => {
    const form = new FormData()
    form.append('chunk', chunk, 'chunk.bin')
    return api.put(`/files/uploads/${uploadId}/chunks`, form, {
      params: { offset, chunk_sha256: chunkSha256 },
    })
  },

  completeUpload: (uploadId: string) =>
    api.post<UploadSession>(`/files/uploads/${uploadId}/complete`),

  cancelUpload: (uploadId: string) =>
    api.delete(`/files/uploads/${uploadId}`),

  createTransfer: (params: {
    upload_id: string
    target_node_ids: string[]
    dest_path: string
    overwrite: boolean
  }) => api.post<FileTransfer>('/files/transfers', params),

  listTransfers: (limit = 50) =>
    api.get<FileTransfer[]>('/files/transfers', { params: { limit } }),

  getTransfer: (transferId: string) =>
    api.get<FileTransfer>(`/files/transfers/${transferId}`),

  retryTransfer: (transferId: string) =>
    api.post<FileTransfer>(`/files/transfers/${transferId}/retry`),
}

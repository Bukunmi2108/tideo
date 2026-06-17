// Typed API client — every backend response shape mirrored here.
// tsc --noEmit is the lint step; a backend shape change = compile error.

export function apiBase(): string {
  return import.meta.env.VITE_API_BASE ?? ""
}

// ---- Response types -------------------------------------------------------

export type JobStatus =
  | "inspecting"
  | "awaiting_choice"
  | "queued"
  | "transcoding"
  | "done"
  | "failed"
  | "cancelled"
  | "expired"

export interface SourceMeta {
  container: string
  video_codec: string
  audio_codec: string | null
  width: number
  height: number
  duration: number
  bitrate: number
  has_audio: boolean
  rotation: number
}

export interface JobError {
  code: string
  message: string
  stage: string | null
  retryable: boolean
}

export interface JobResults {
  master: string
  web_mp4: string
  manifest: string
}

export interface JobResponse {
  job_id: string
  status: JobStatus
  source?: SourceMeta
  recommended_presets?: string[]
  web_safe?: boolean
  web_safe_reason?: string | null
  progress?: Record<string, number>
  results?: JobResults
  error?: JobError
}

export interface UploadResponse {
  job_id: string
  status: string
  dedupe: "hit" | "miss"
}

export interface TranscodeRequest {
  presets: string[]
  subtitles: boolean
}

// ---- Error type -----------------------------------------------------------

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly jobId: string | null,
    public readonly retryable: boolean,
  ) {
    super(message)
    this.name = "ApiError"
  }
}

// ---- Fetchers -------------------------------------------------------------

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${apiBase()}${path}`, init)
  const body = await resp.json()
  if (!resp.ok) {
    const e = body?.error ?? {}
    throw new ApiError(resp.status, e.code ?? "UNKNOWN", e.message ?? "request failed", e.job_id ?? null, e.retryable ?? false)
  }
  return body as T
}

export function getJob(jobId: string): Promise<JobResponse> {
  return request<JobResponse>(`/jobs/${jobId}`)
}

export function postTranscode(jobId: string, body: TranscodeRequest): Promise<{ job_id: string; status: string }> {
  return request(`/jobs/${jobId}/transcode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
}

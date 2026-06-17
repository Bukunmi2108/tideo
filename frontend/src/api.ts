// Typed API client — every backend response shape mirrored here.
// tsc --noEmit is the lint step; a backend shape change = compile error.

export function apiBase(): string {
  return import.meta.env.VITE_API_BASE ?? "";
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
  | "expired";

export interface SourceMeta {
  container: string;
  video_codec: string | null;
  audio_codec: string | null;
  width: number;
  height: number;
  duration: number;
  bitrate: number | null;
  fps: number | null;
  has_audio: boolean;
  video_streams: number;
  audio_streams: number;
}

export interface JobError {
  code: string;
  message: string;
  stage: string | null;
  retryable: boolean;
}

export interface SubtitleStatus {
  status: "processing" | "ready" | "failed" | "none";
  url?: string;
  reason?: string;
  code?: string;
}

export interface JobResults {
  playlist: string; // master.m3u8 URL for hls.js
  web_mp4: string;
  poster: string;
  sprite: string;
  player: string; // embed player page; snippet composed from this
  presets: string[];
  duration: number | null;
  subtitles?: SubtitleStatus | null; // null when captions weren't requested
}

export interface JobResponse {
  job_id: string;
  status: JobStatus;
  source?: SourceMeta;
  source_filename?: string | null;
  recommended_presets?: string[];
  web_safe?: boolean;
  web_safe_reason?: string | null;
  presets?: string[];
  progress?: Record<string, number>;
  results?: JobResults;
  error?: JobError;
}

export interface JobSummary {
  job_id: string;
  status: JobStatus;
  source_filename: string | null;
  duration: number | null;
  created_at: string | null;
  finished_at: string | null;
  expires_at: string | null;
  poster: string | null;
}

export interface JobListResponse {
  items: JobSummary[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface UploadResponse {
  job_id: string;
  status: string;
  dedupe: "hit" | "miss";
}

export interface TranscodeRequest {
  presets: string[];
  subtitles: boolean;
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
    super(message);
    this.name = "ApiError";
  }
}

// ---- Fetchers -------------------------------------------------------------

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${apiBase()}${path}`, init);
  const body = await resp.json();
  if (!resp.ok) {
    const e = body?.error ?? {};
    throw new ApiError(
      resp.status,
      e.code ?? "UNKNOWN",
      e.message ?? "request failed",
      e.job_id ?? null,
      e.retryable ?? false,
    );
  }
  return body as T;
}

export function getJob(jobId: string): Promise<JobResponse> {
  return request<JobResponse>(`/jobs/${jobId}`);
}

export function listJobs(
  opts: { status?: string; limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<JobListResponse> {
  const q = new URLSearchParams();
  if (opts.status) q.set("status", opts.status);
  if (opts.limit != null) q.set("limit", String(opts.limit));
  if (opts.offset != null) q.set("offset", String(opts.offset));
  const qs = q.toString();
  return request<JobListResponse>(`/jobs${qs ? `?${qs}` : ""}`, { signal });
}

export function postTranscode(
  jobId: string,
  body: TranscodeRequest,
): Promise<{ job_id: string; status: string }> {
  return request(`/jobs/${jobId}/transcode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function postCancel(
  jobId: string,
): Promise<{ job_id: string; status: string }> {
  return request(`/jobs/${jobId}/cancel`, { method: "POST" });
}

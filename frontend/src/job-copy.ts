import type { JobError, JobResponse } from "./api";

export function friendlyContainer(container: string): string {
  const formats = new Set(container.toLowerCase().split(","));
  if (formats.has("mp4")) return "MP4";
  if (formats.has("mov")) return "QuickTime (MOV)";
  if (formats.has("webm")) return "WebM";
  if (formats.has("matroska")) return "Matroska (MKV)";
  if (formats.has("avi")) return "AVI";
  return container ? container.toUpperCase() : "Not available";
}

export function friendlyCodec(codec: string | null): string {
  const labels: Record<string, string> = {
    h264: "H.264 (AVC)",
    hevc: "H.265 (HEVC)",
    av1: "AV1",
    vp9: "VP9",
    aac: "AAC",
    opus: "Opus",
    mp3: "MP3",
    ac3: "Dolby Digital (AC-3)",
  };
  if (!codec) return "Not available";
  return labels[codec.toLowerCase()] ?? codec.toUpperCase();
}

export function readinessExplanation(job: JobResponse): string {
  const source = job.source!;
  if (job.web_safe === true) {
    return source.has_audio
      ? "This source already uses web-compatible H.264 video and AAC audio in an MP4-compatible format."
      : "This source already uses web-compatible H.264 video in an MP4-compatible format.";
  }
  const needs: string[] = [];
  if (source.video_codec !== "h264") needs.push("H.264 video");
  if (source.has_audio && source.audio_codec !== "aac") needs.push("AAC audio");
  const formats = source.container.toLowerCase().split(",");
  if (!formats.includes("mp4") && !formats.includes("mov"))
    needs.push("an MP4 or MOV container");
  return `This source needs ${naturalList(needs)} for reliable browser playback.`;
}

function naturalList(values: string[]): string {
  if (values.length < 2) return values[0] ?? "a compatible format";
  if (values.length === 2) return values.join(" and ");
  return `${values.slice(0, -1).join(", ")}, and ${values.at(-1)}`;
}

export function failureMessage(error?: JobError): string {
  const messages: Record<string, string> = {
    SOURCE_CORRUPT:
      "This file appears damaged or incomplete. Try a different copy of the video.",
    SOURCE_UNSUPPORTED:
      "This video uses a codec Tideo cannot process yet. Export it as an H.264 MP4 and upload it again.",
    SOURCE_NO_VIDEO:
      "This file does not contain a video track. Choose a video file and upload it again.",
    SOURCE_LIMITS_EXCEEDED:
      "This video exceeds the public demo limits. Choose a shorter or lower-resolution file.",
    INSPECTION_UNAVAILABLE:
      "The inspection service is temporarily unavailable. Upload the video again to retry.",
    ENCODE_FAILED_TRANSIENT:
      "The processing service became unavailable before transcoding finished. Upload the video again to retry.",
    ENCODE_TIMEOUT:
      "Transcoding took longer than the public demo allows. Try a shorter video or fewer output qualities.",
    STORAGE_FULL:
      "Tideo does not have enough temporary storage right now. Try again later.",
    JOB_STALE:
      "This job stopped responding and was closed. Upload the video again to retry.",
  };
  if (error?.code && messages[error.code]) return messages[error.code];
  return error?.retryable
    ? "Tideo couldn’t complete this job because the processing service became unavailable. Upload the video again to retry."
    : "Tideo couldn’t process this video. Try another file or export it as an H.264 MP4.";
}

import { describe, expect, it } from "vitest";
import {
  failureMessage,
  friendlyCodec,
  friendlyContainer,
  readinessExplanation,
} from "./job-copy";

describe("job copy", () => {
  it("turns ffprobe format and codec values into product labels", () => {
    expect(friendlyContainer("mov,mp4,m4a,3gp,3g2,mj2")).toBe("MP4");
    expect(friendlyContainer("matroska,webm")).toBe("WebM");
    expect(friendlyCodec("h264")).toBe("H.264 (AVC)");
    expect(friendlyCodec("opus")).toBe("Opus");
  });

  it("explains every compatibility change without exposing backend syntax", () => {
    expect(
      readinessExplanation({
        job_id: "j1",
        status: "awaiting_choice",
        web_safe: false,
        source: {
          container: "matroska,webm",
          video_codec: "vp9",
          audio_codec: "opus",
          width: 1280,
          height: 720,
          duration: 60,
          bitrate: 2_000_000,
          fps: 30,
          has_audio: true,
          video_streams: 1,
          audio_streams: 1,
        },
      }),
    ).toContain("needs H.264 video, AAC audio, and an MP4 or MOV container");
  });

  it("maps backend diagnostics to user-facing recovery copy", () => {
    const message = failureMessage({
      code: "SOURCE_CORRUPT",
      message: "moov atom not found /srv/private/input",
      stage: "inspect",
      retryable: false,
    });
    expect(message).toContain("appears damaged or incomplete");
    expect(message).not.toContain("moov atom");
  });
});

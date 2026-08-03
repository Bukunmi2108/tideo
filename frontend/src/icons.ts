import checkCircle from "@phosphor-icons/core/regular/check-circle.svg?raw";
import play from "@phosphor-icons/core/regular/play.svg?raw";
import spinnerGap from "@phosphor-icons/core/regular/spinner-gap.svg?raw";
import uploadSimple from "@phosphor-icons/core/regular/upload-simple.svg?raw";
import videoCamera from "@phosphor-icons/core/regular/video-camera.svg?raw";
import xCircle from "@phosphor-icons/core/regular/x-circle.svg?raw";

const ICONS = {
  check: checkCircle,
  error: xCircle,
  play,
  spinner: spinnerGap,
  upload: uploadSimple,
  video: videoCamera,
} as const;

export type IconName = keyof typeof ICONS;

/** Render a trusted Phosphor asset as a decorative inline SVG. */
export function icon(name: IconName): string {
  return ICONS[name].replace(
    "<svg ",
    `<svg class="icon icon--${name}" aria-hidden="true" focusable="false" `,
  );
}

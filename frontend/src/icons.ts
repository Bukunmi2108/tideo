import arrowClockwise from "@phosphor-icons/core/regular/arrow-clockwise.svg?raw";
import caretDown from "@phosphor-icons/core/regular/caret-down.svg?raw";
import checkCircle from "@phosphor-icons/core/regular/check-circle.svg?raw";
import closedCaptioning from "@phosphor-icons/core/regular/closed-captioning.svg?raw";
import copy from "@phosphor-icons/core/regular/copy.svg?raw";
import cornersOut from "@phosphor-icons/core/regular/corners-out.svg?raw";
import downloadSimple from "@phosphor-icons/core/regular/download-simple.svg?raw";
import pause from "@phosphor-icons/core/regular/pause.svg?raw";
import play from "@phosphor-icons/core/regular/play.svg?raw";
import shareNetwork from "@phosphor-icons/core/regular/share-network.svg?raw";
import speakerHigh from "@phosphor-icons/core/regular/speaker-high.svg?raw";
import speakerX from "@phosphor-icons/core/regular/speaker-x.svg?raw";
import spinnerGap from "@phosphor-icons/core/regular/spinner-gap.svg?raw";
import uploadSimple from "@phosphor-icons/core/regular/upload-simple.svg?raw";
import videoCamera from "@phosphor-icons/core/regular/video-camera.svg?raw";
import xCircle from "@phosphor-icons/core/regular/x-circle.svg?raw";

const ICONS = {
  caret: caretDown,
  captions: closedCaptioning,
  check: checkCircle,
  copy,
  download: downloadSimple,
  error: xCircle,
  fullscreen: cornersOut,
  pause,
  play,
  retry: arrowClockwise,
  share: shareNetwork,
  speaker: speakerHigh,
  muted: speakerX,
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

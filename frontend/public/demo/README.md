# Demo media

The Overview uses a checked-in adaptive HLS package of the 52-second **Sintel**
trailer. It is independent of Tideo's processing and transcription services, so
quality switching and captions work while the backend is unavailable or asleep.

The package contains:

- 480p, 360p, and 240p H.264/AAC renditions;
- two-second aligned GOPs for responsive manual switching;
- one byte-range fMP4 media file per rendition instead of dozens of segment files;
- a preprocessed English WebVTT track;
- a poster and eight-frame seek storyboard.

The video was cropped to remove source letterboxing, then resized and re-encoded
for adaptive playback. The captions were generated from its audio and reviewed
against all four spoken lines. The poster and storyboard use the same crop.

## Attribution

**Sintel** trailer © Blender Foundation / Durian Open Movie Team.
Licensed under [Creative Commons Attribution 3.0](https://creativecommons.org/licenses/by/3.0/).
Project and attribution guidance: [durian.blender.org](https://durian.blender.org/sharing/).

The source used for this package was `01_sintel_cinematic.mp4` (854×480,
52.208 seconds). Rebuild from an equivalent source using two-second HLS segments,
closed 48-frame GOPs at 24 fps, and the following measured ladder:

| Rendition | Average bandwidth | Peak bandwidth |
|---|---:|---:|
| 480p | 516 kbps | 1,121 kbps |
| 360p | 340 kbps | 709 kbps |
| 240p | 202 kbps | 403 kbps |

Regenerate the visual assets from the source with:

```bash
ffmpeg -ss 00:00:26 -i 01_sintel_cinematic.mp4 -frames:v 1 -vf "crop=854:364:0:58,scale=-2:480:flags=lanczos,crop=854:480,setsar=1" -quality 80 frontend/public/demo/sintel-cinematic-poster.webp
ffmpeg -i 01_sintel_cinematic.mp4 -vf "fps=8/52.208333,crop=854:364:0:58,scale=-2:180:flags=lanczos,crop=320:180,setsar=1,tile=4x2" -frames:v 1 -quality 74 frontend/public/demo/sintel-cinematic-storyboard.webp
```

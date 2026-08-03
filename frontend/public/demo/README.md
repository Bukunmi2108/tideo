# Demo media

These poster and storyboard images come from `fixtures/_demo.mp4`, Tideo's synthetic test-pattern fixture. They contain no user media and keep the Overview useful when the API is unavailable.

Regenerate them from the repository root with:

```bash
ffmpeg -ss 00:00:02 -i fixtures/_demo.mp4 -frames:v 1 -vf "scale=1280:720:flags=lanczos" -quality 78 frontend/public/demo/tideo-test-pattern-poster.webp
ffmpeg -i fixtures/_demo.mp4 -vf "fps=2/3,scale=320:180:flags=lanczos,tile=4x2" -frames:v 1 -quality 72 frontend/public/demo/tideo-test-pattern-storyboard.webp
```

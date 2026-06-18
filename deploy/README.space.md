---
title: Tideo
emoji: 🎬
colorFrom: red
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
short_description: Distributed video transcoding — adaptive HLS on demand
---

# Tideo

Upload a video, get an adaptive HLS ladder back — every resolution encoded in parallel.

This Space runs the **entire stack in one container** (the only topology HF Spaces allows):
Postgres · Redis · RabbitMQ · Kafka (KRaft) · FastAPI · Celery workers · dispatcher · beat,
all under `supervisord`, exposed on port 7860.

- **API + embed player:** this Space URL (`/docs`, `/jobs/{id}/player`).
- **Web app:** the Vercel frontend (set `VITE_API_BASE` to this Space URL).
- **Ephemeral:** disk resets on restart; demo outputs live ~24h. Cold starts show a "waking up" state.

Set `ADMIN_TOKEN` in the Space **Settings → Secrets**; it gates all `/admin/*` routes.

Source + architecture: https://github.com/Bukunmi2108/tideo

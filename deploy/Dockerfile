# Tideo — full stack in one image for an HF Docker Space. Runs as uid 1000 under supervisord.
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg postgresql postgresql-contrib redis-server rabbitmq-server \
      default-jre-headless supervisor curl ca-certificates procps \
 && rm -rf /var/lib/apt/lists/*

# Kafka (KRaft, single node)
ARG KAFKA_VERSION=3.7.2
ARG SCALA_VERSION=2.13
RUN curl -fsSL "https://archive.apache.org/dist/kafka/${KAFKA_VERSION}/kafka_${SCALA_VERSION}-${KAFKA_VERSION}.tgz" -o /tmp/kafka.tgz \
 && mkdir -p /opt/kafka \
 && tar -xzf /tmp/kafka.tgz -C /opt/kafka --strip-components=1 \
 && rm /tmp/kafka.tgz

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:/opt/kafka/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache

# bake the whisper model so there's no download at cold boot
ARG STT_MODEL=base
ENV HF_HOME=/opt/models
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${STT_MODEL}', device='cpu', compute_type='int8')"

RUN useradd -m -u 1000 user \
 && mkdir -p /data /data/logs /data/home \
 && chown -R user:user /opt/models \
 && chmod -R 777 /data

COPY --chown=user:user app/ ./app/
COPY --chown=user:user deploy/ ./deploy/
RUN cp deploy/.env.deploy /app/.env \
 && cp deploy/kraft-tideo.properties /opt/kafka/config/kraft-tideo.properties \
 && chmod +x deploy/scripts/*.sh

USER user
ENV HOME=/data/home

EXPOSE 7860
CMD ["supervisord", "-c", "/app/deploy/supervisord.conf"]

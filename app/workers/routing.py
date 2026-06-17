from kombu import Queue

QUEUE_NAMES = ("inspect", "transcode", "package", "transcribe", "cleanup", "dead-letter")
task_queues = tuple(Queue(name) for name in QUEUE_NAMES)


task_routes = {
    "app.workers.tasks.inspect.*":    {"queue": "inspect"},
    "app.workers.tasks.transcode.*":  {"queue": "transcode"},
    "app.workers.tasks.rendition.*":  {"queue": "transcode"},   # heavy encode -> heavy lane
    "app.workers.tasks.package.*":    {"queue": "package"},
    "app.workers.tasks.transcribe.*": {"queue": "transcribe"},
    "app.workers.tasks.cleanup.*":    {"queue": "cleanup"},
}
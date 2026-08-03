from celery import Task

from app.core.config import config


class InspectTask(Task):
    time_limit = 30


class PackageTask(Task):
    soft_time_limit = 240
    time_limit = 300


class TranscribeTask(Task):
    soft_time_limit = 540
    time_limit = 600


class CleanupTask(Task):
    time_limit = 300
    acks_late = False


class TranscodeTask(Task):
    soft_time_limit = config.transcode_max_seconds
    time_limit = config.transcode_max_seconds + 60

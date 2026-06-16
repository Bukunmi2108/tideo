import json
import socket
from dataclasses import asdict, dataclass, field
from uuid import uuid4
from app.api.utils import now_iso


SCHEMA_VERSION = 1
PRODUCER = socket.gethostname()


@dataclass(frozen=True)
class Envelope:
    event_type: str
    job_id: str
    payload: dict
    schema_version: int = SCHEMA_VERSION
    producer: str = PRODUCER
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(default_factory=now_iso)

    def to_json(self) -> str:
        return json.dumps(asdict(self))
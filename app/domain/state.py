import logging

logger = logging.getLogger(__name__)

TERMINAL = {"done", "failed", "cancelled", "expired"}

TRANSITIONS = {
    "inspecting":      {"awaiting_choice", "failed"},
    "awaiting_choice": {"queued", "failed"},
    "queued":          {"transcoding", "cancelled", "failed"},
    "transcoding":     {"done", "cancelled", "failed"},
    "done":            {"expired"},
    "failed":          set(),
    "cancelled":       set(),
    "expired":         set(),
}

class IllegalTransition(Exception):
    pass

def transition(current: str, new: str, *, job_id: str = "", caller: str = "") -> str | None:
    if new in TRANSITIONS.get(current, set()):
        return new
    if current in TERMINAL:
        logger.info("dropped %s->%s (terminal) job=%s caller=%s", current, new, job_id, caller)
        return None
    raise IllegalTransition(f"{current} -> {new} (job={job_id} caller={caller})")
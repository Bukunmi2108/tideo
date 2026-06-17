from app.core.logging import get_logger

log = get_logger()

TERMINAL = {"done", "failed", "cancelled", "expired"}
ACTIVE = {"inspecting", "awaiting_choice", "queued", "transcoding"}

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
        log.info("transition_dropped", from_state=current, to_state=new, job_id=job_id, caller=caller)
        return None
    raise IllegalTransition(f"{current} -> {new} (job={job_id} caller={caller})")
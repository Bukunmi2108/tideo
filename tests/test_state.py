import logging

import pytest

from app.domain.state import (
    TERMINAL,
    TRANSITIONS,
    IllegalTransition,
    transition,
)


# ---- allowed transitions ----

@pytest.mark.parametrize("current,allowed", [(s, TRANSITIONS[s]) for s in TRANSITIONS])
def test_every_allowed_pair_returns_target(current, allowed):
    for nxt in allowed:
        assert transition(current, nxt) == nxt


# ---- the full from x to matrix ----

def _classify(current, new):
    if new in TRANSITIONS.get(current, set()):
        return "allow"
    if current in TERMINAL:
        return "drop"
    return "raise"


@pytest.mark.parametrize("current", list(TRANSITIONS))
@pytest.mark.parametrize("new", list(TRANSITIONS))
def test_full_matrix(current, new):
    kind = _classify(current, new)
    if kind == "allow":
        assert transition(current, new) == new
    elif kind == "drop":
        assert transition(current, new) is None
    else:
        with pytest.raises(IllegalTransition):
            transition(current, new)


# ---- terminal-state late events are dropped, not raised ----

@pytest.mark.parametrize("term", sorted(TERMINAL))
def test_terminal_drops_any_incoming_event(term, caplog):
    with caplog.at_level(logging.INFO, logger="app.domain.state"):
        # a straggler trying to push a terminal job anywhere is silently dropped
        assert transition(term, "done") is None
        assert transition(term, "transcoding") is None
    assert any("dropped" in r.message for r in caplog.records)


def test_true_sinks_have_no_outgoing_transitions():
    # done is "almost terminal" — it may still expire; the rest are sinks.
    for t in TERMINAL - {"done"}:
        assert TRANSITIONS[t] == set()
    assert TRANSITIONS["done"] == {"expired"}


# ---- illegal non-terminal transitions raise with context ----

def test_illegal_transition_names_both_states_and_caller():
    with pytest.raises(IllegalTransition) as exc:
        transition("inspecting", "done", job_id="j_1", caller="bug")
    msg = str(exc.value)
    assert "inspecting" in msg and "done" in msg and "j_1" in msg and "bug" in msg


def test_self_transition_is_illegal_for_non_terminal():
    # inspecting -> inspecting is not in the table and inspecting is not terminal
    with pytest.raises(IllegalTransition):
        transition("inspecting", "inspecting")


def test_unknown_current_state_raises():
    with pytest.raises(IllegalTransition):
        transition("not_a_state", "done")

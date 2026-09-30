"""Version-pinned election, cleanup and discovery under concurrent starts."""

import time
from collections import defaultdict

import httpx
import pytest

from kdev import api, persistence, session
from kdev.errors import KdevError

CREDS = api.Creds("alice")
SLUG = "owner/box"


@pytest.fixture
def clock(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(time, "sleep", lambda delay: elapsed.__setitem__(0, elapsed[0] + delay))
    return elapsed


@pytest.fixture
def runs(monkeypatch, clock):
    states = defaultdict(lambda: "COMPLETE")
    reads = []

    def status(creds, slug, version=""):
        assert slug == SLUG
        reads.append(version)
        value = states[version]
        if isinstance(value, list):
            value = value.pop(0) if len(value) > 1 else value[0]
        if isinstance(value, Exception):
            raise value
        return {"status": value}

    monkeypatch.setattr(api, "session_status", status)
    monkeypatch.setattr(api, "get_kernel", lambda *a: {"currentVersionNumber": 20})
    monkeypatch.setattr(persistence, "version_files", lambda *a: {})
    return states, reads


@pytest.mark.parametrize("value,expected", [(1, 1), ("16", 16), (0, 0), ("001", 1)])
def test_version_number_accepts_only_exact_nonnegative_numbers(value, expected):
    assert session.version_number(value) == expected


@pytest.mark.parametrize(
    "value", [None, True, False, 1.2, -1, "-1", "1.2", "", "v1", " 1", [], "1" * 5000]
)
def test_invalid_save_version_is_never_guessed(value):
    with pytest.raises(KdevError):
        session.version_number(value)


@pytest.mark.parametrize("state", ["RUNNING", "QUEUED"])
def test_lowest_live_version_wins_even_with_many_contenders(runs, state):
    states, reads = runs
    states.update({f"v{n}": state for n in range(11, 21)})
    assert session.start_winner(CREDS, SLUG, 20, 10) == "v11"
    assert "" not in reads


@pytest.mark.parametrize("state", ["COMPLETE", "ERROR", "CANCEL_ACKNOWLEDGED", "CANCEL_REQUESTED"])
def test_ended_and_saving_runs_cannot_win(runs, state):
    states, _ = runs
    states.update({"v11": state, "v12": "RUNNING"})
    assert session.start_winner(CREDS, SLUG, 13, 10) == "v12"


def test_newly_created_status_is_retried_until_visible(runs):
    states, reads = runs
    states["v11"] = ["UNKNOWN", "UNKNOWN", "QUEUED"]
    assert session.start_winner(CREDS, SLUG, 12, 10) == "v11"
    assert reads.count("v11") == 3


def test_source_only_historical_version_does_not_block_a_start(runs):
    states, _ = runs
    states["v10"] = "UNKNOWN"
    assert session.start_winner(CREDS, SLUG, 11, 10) == "v11"


def test_first_start_has_no_historical_reads(runs):
    _, reads = runs
    assert session.start_winner(CREDS, SLUG, 1, 0) == "v1"
    assert reads == []


@pytest.mark.parametrize(
    "failure", [api.KaggleError("denied", 403), httpx.ConnectError("offline"), KeyboardInterrupt()]
)
def test_election_failure_cleans_up_our_run_before_propagating(monkeypatch, failure):
    cancelled = []

    def fail(*args):
        raise failure

    monkeypatch.setattr(session, "start_winner", fail)
    monkeypatch.setattr(session, "cancel_created", lambda c, s, v: cancelled.append(v))
    with pytest.raises(type(failure)):
        session.resolve_start(CREDS, SLUG, 12, 10)
    assert cancelled == ["v12"]


def test_unknown_new_version_times_out_and_cleans_up_own_submission(runs, monkeypatch):
    states, _ = runs
    states["v11"] = "UNKNOWN"
    cancelled = []
    monkeypatch.setattr(session, "cancel_created", lambda c, s, v: cancelled.append(v))
    with pytest.raises(KdevError, match="not reported"):
        session.resolve_start(CREDS, SLUG, 12, 10)
    assert cancelled == ["v12"]


@pytest.mark.parametrize("version,previous", [(10, 10), (9, 10), (1000, 10)])
def test_stale_or_invalid_metadata_fails_closed_with_cleanup(runs, monkeypatch, version, previous):
    cancelled = []
    monkeypatch.setattr(session, "cancel_created", lambda c, s, v: cancelled.append(v))
    with pytest.raises(KdevError, match="safely resolve"):
        session.resolve_start(CREDS, SLUG, version, previous)
    assert cancelled == [f"v{version}"]


def test_loser_cancels_only_its_pinned_session_as_the_starter(runs, monkeypatch):
    states, reads = runs
    states.update({"v11": "RUNNING", "v12": "RUNNING"})
    monkeypatch.setattr(
        api,
        "stream_logs",
        lambda c, s, **kw: iter(
            [f"KDEV_SESSION id={12 if kw['version'] == 'v12' else 11} by=alice"]
        ),
    )
    cancelled = []

    def cancel(creds, sid):
        cancelled.append((creds.username, sid))
        states["v12"] = ["CANCEL_REQUESTED", "CANCEL_ACKNOWLEDGED"]

    monkeypatch.setattr(api, "cancel_session", cancel)
    assert session.resolve_start(CREDS, SLUG, 12, 10) == "v11"
    assert cancelled == [("alice", 12)] and "" not in reads


def test_winner_ending_during_cleanup_does_not_revive_stopped_loser(runs, monkeypatch):
    states, _ = runs
    states["v11"] = "RUNNING"

    def stop(*args):
        states["v11"] = "COMPLETE"

    monkeypatch.setattr(session, "cancel_created", stop)
    with pytest.raises(KdevError, match="other session ended"):
        session.resolve_start(CREDS, SLUG, 12, 10)


def test_already_ended_duplicate_needs_no_log_or_cancel(runs, monkeypatch):
    monkeypatch.setattr(api, "stream_logs", lambda *a, **kw: pytest.fail("no log read"))
    monkeypatch.setattr(api, "cancel_session", lambda *a: pytest.fail("no cancel"))
    session.cancel_created(CREDS, SLUG, "v12")


def test_duplicate_ending_before_its_id_is_visible_needs_no_cancel(runs, monkeypatch):
    states, _ = runs
    states["v12"] = ["QUEUED", "COMPLETE"]
    monkeypatch.setattr(session, "live_session_id", lambda *a, **kw: ("", ""))
    monkeypatch.setattr(api, "cancel_session", lambda *a: pytest.fail("already ended"))
    session.cancel_created(CREDS, SLUG, "v12")


@pytest.mark.parametrize("ended", [True, False])
def test_cancel_error_requires_independent_confirmation_of_stop(runs, monkeypatch, ended):
    states, _ = runs
    states["v12"] = "RUNNING"
    monkeypatch.setattr(session, "live_session_id", lambda *a, **kw: ("12", "alice"))

    def failed_cancel(*a):
        if ended:
            states["v12"] = "COMPLETE"
        raise api.KaggleError("cancel failed", 403)

    monkeypatch.setattr(api, "cancel_session", failed_cancel)
    if ended:
        session.cancel_created(CREDS, SLUG, "v12")
    else:
        with pytest.raises(KdevError, match="Could not confirm") as error:
            session.cancel_created(CREDS, SLUG, "v12")
        assert "kdev down --session-id 12" in error.value.hint


@pytest.mark.parametrize("latest", [10, 500])
def test_inconsistent_metadata_cannot_make_a_shared_tunnel_ready(runs, monkeypatch, latest):
    states, _ = runs
    states["v11"] = "RUNNING"
    monkeypatch.setattr(api, "get_kernel", lambda *a: {"currentVersionNumber": latest})
    with pytest.raises(KdevError, match="Cannot verify"):
        session.wait_single(CREDS, SLUG, "v11", timeout=3)


def test_unreadable_startup_state_cannot_hide_an_older_winner(runs, monkeypatch):
    monkeypatch.setattr(persistence, "version_files", lambda *a: {persistence.STATE: "bad-url"})
    monkeypatch.setattr(persistence, "_state", lambda f: None)
    with pytest.raises(KdevError, match="saved startup state"):
        session.current_status(CREDS, SLUG)


def test_log_read_deadline_cannot_be_extended_by_continuous_output(runs, monkeypatch, clock):
    states, _ = runs
    states["v12"] = "RUNNING"

    def logs(*a, **kw):
        for _ in range(20):
            clock[0] += 1
            yield "still booting"

    monkeypatch.setattr(api, "stream_logs", logs)
    assert session.await_ready(CREDS, SLUG, timeout=4, version="v12") is None
    assert clock[0] == 4


def test_different_starter_is_never_cancelled(runs, monkeypatch):
    states, _ = runs
    states["v12"] = "RUNNING"
    monkeypatch.setattr(session, "live_session_id", lambda *a, **kw: ("12", "bob"))
    monkeypatch.setattr(api, "cancel_session", lambda *a: pytest.fail("wrong owner"))
    with pytest.raises(KdevError, match="different starter"):
        session.cancel_created(CREDS, SLUG, "v12")


@pytest.mark.parametrize("state", ["RUNNING", "CANCEL_REQUESTED", "UNKNOWN"])
def test_cancel_request_alone_does_not_count_as_stopped(runs, monkeypatch, state):
    states, _ = runs
    states["v12"] = state
    monkeypatch.setattr(session, "live_session_id", lambda *a, **kw: ("12", "alice"))
    monkeypatch.setattr(api, "cancel_session", lambda *a: {})
    with pytest.raises(KdevError, match="not finished stopping"):
        session.cancel_created(CREDS, SLUG, "v12", timeout=5)


def test_winner_waits_for_every_later_contender_to_finish_saving(runs):
    states, reads = runs
    states.update(
        {
            "v11": "RUNNING",
            "v12": ["RUNNING", "CANCEL_REQUESTED", "CANCEL_ACKNOWLEDGED"],
            "v20": ["RUNNING", "COMPLETE"],
        }
    )
    session.wait_single(CREDS, SLUG, "v11", timeout=30)
    assert reads.count("v12") >= 3 and reads.count("v20") == 2


def test_live_contender_timeout_never_reports_shared_tunnel_safe(runs):
    states, _ = runs
    states.update({"v11": "RUNNING", "v20": "RUNNING"})
    with pytest.raises(KdevError, match="not safe"):
        session.wait_single(CREDS, SLUG, "v11", timeout=6)


def test_stopped_winner_is_not_ready(runs):
    with pytest.raises(KdevError, match="ended before"):
        session.wait_single(CREDS, SLUG, "v11")


def test_cancelled_latest_cannot_hide_an_older_live_winner(runs):
    states, _ = runs
    states.update(
        {"": "CANCEL_ACKNOWLEDGED", "v20": "CANCEL_ACKNOWLEDGED", "v17": "RUNNING", "v19": "QUEUED"}
    )
    label, state = session.current_status(CREDS, SLUG)
    assert label == "v17" and state["status"] == "RUNNING"


def test_cancelled_latest_cannot_hide_a_winner_still_saving(runs):
    states, _ = runs
    states.update({"v20": "CANCEL_ACKNOWLEDGED", "v17": "CANCEL_REQUESTED"})
    label, state = session.current_status(CREDS, SLUG)
    assert label == "v17" and state["status"] == api.SAVING


def test_pending_save_is_not_preferred_over_a_live_winner(runs):
    states, _ = runs
    states.update({"v20": "CANCEL_REQUESTED", "v17": "RUNNING"})
    assert session.current_status(CREDS, SLUG)[0] == "v17"


def test_unconfirmed_discovery_limit_requires_inspection(runs, monkeypatch):
    monkeypatch.setattr(api, "get_kernel", lambda *a: {"currentVersionNumber": 1000})
    monkeypatch.setattr(persistence, "version_files", lambda *a: {"state": "url"})
    monkeypatch.setattr(persistence, "_state", lambda f: {"startup_confirmed": False})
    with pytest.raises(KdevError, match="Too many unconfirmed"):
        session.current_status(CREDS, SLUG)


def test_rejected_save_waits_for_accepted_version_to_be_visible(runs, monkeypatch):
    versions = iter([("v11", {"status": "UNKNOWN"}), ("v11", {"status": "QUEUED"})])
    monkeypatch.setattr(session, "current_status", lambda *a: next(versions))
    assert session.rejected_start(CREDS, SLUG, timeout=5) == "v11"


def test_rejected_save_without_any_winner_is_not_success(runs):
    assert session.rejected_start(CREDS, SLUG, timeout=4) == ""


def test_pinned_save_wait_keeps_waiting_through_cancel_requested(runs):
    states, reads = runs
    states["v17"] = ["RUNNING", "CANCEL_REQUESTED", "CANCEL_ACKNOWLEDGED"]
    session.wait_saved(CREDS, SLUG, running_too=True, timeout=20, version="v17")
    assert reads == ["v17", "v17", "v17"]


def test_temporarily_missing_status_cannot_end_a_known_save_wait(runs):
    states, reads = runs
    states["v17"] = ["CANCEL_REQUESTED", "UNKNOWN", "CANCEL_ACKNOWLEDGED"]
    session.wait_saved(CREDS, SLUG, timeout=20, version="v17")
    assert reads == ["v17", "v17", "v17"]


def test_unknown_run_being_replaced_is_not_reported_saved(runs):
    states, _ = runs
    states["v17"] = "UNKNOWN"
    with pytest.raises(KdevError, match="still saving"):
        session.wait_saved(CREDS, SLUG, running_too=True, timeout=3, version="v17")


def test_discovery_pins_latest_status_despite_metadata_logical_race(runs):
    states, _ = runs
    states.update({"": "RUNNING", "v20": "ERROR"})
    label, state = session.current_status(CREDS, SLUG)
    assert label == "v20" and state["status"] == "ERROR"


def test_confirmed_history_bounds_discovery_without_hiding_winner(runs, monkeypatch):
    states, reads = runs
    states["v17"] = "RUNNING"
    monkeypatch.setattr(persistence, "version_files", lambda c, s, v: {"state": v})
    monkeypatch.setattr(
        persistence, "_state", lambda files: {"startup_confirmed": files["state"] == "v16"}
    )
    assert session.current_status(CREDS, SLUG)[0] == "v17"
    assert "v15" not in reads


def test_many_unconfirmed_losers_do_not_hide_winner(runs, monkeypatch):
    states, _ = runs
    states["v1"] = "RUNNING"
    monkeypatch.setattr(persistence, "version_files", lambda *a: {"state": "url"})
    monkeypatch.setattr(persistence, "_state", lambda f: {"startup_confirmed": False})
    assert session.current_status(CREDS, SLUG)[0] == "v1"


def test_log_reconnects_after_queued_empty_stream_and_transient_failure(runs, monkeypatch):
    states, _ = runs
    states["v12"] = "QUEUED"
    attempts = []

    def logs(creds, slug, **kw):
        attempts.append(kw)
        if len(attempts) == 1:
            raise api.KaggleError("logs not visible", 404)
        return iter(
            []
            if len(attempts) == 2
            else ["KDEV_SESSION id=12 by=alice", "KDEV_READY host=winner.example"]
        )

    monkeypatch.setattr(api, "stream_logs", logs)
    seen = {}
    assert (
        session.await_ready(CREDS, SLUG, timeout=10, seen=seen, version="v12") == "winner.example"
    )
    assert seen == {"session": "12", "by": "alice"}
    assert len(attempts) == 3 and all(a["version"] == "v12" and a["idle"] <= 10 for a in attempts)


def test_completed_run_ready_marker_cannot_connect_to_dead_tunnel(runs, monkeypatch):
    monkeypatch.setattr(
        api, "stream_logs", lambda *a, **kw: iter(["KDEV_READY host=stale.example"])
    )
    assert session.await_ready(CREDS, SLUG, timeout=10, version="v12") is None


def test_permission_errors_in_pinned_logs_are_not_retried(runs, monkeypatch):
    def logs(*a, **kw):
        raise api.KaggleError("denied", 403)

    monkeypatch.setattr(api, "stream_logs", logs)
    with pytest.raises(api.KaggleError, match="denied"):
        session.await_ready(CREDS, SLUG, timeout=10, version="v12")


def test_missing_session_id_is_bounded_and_never_cancels(runs, monkeypatch):
    states, _ = runs
    states["v12"] = "QUEUED"
    monkeypatch.setattr(api, "stream_logs", lambda *a, **kw: iter([]))
    monkeypatch.setattr(api, "cancel_session", lambda *a: pytest.fail("no id"))
    with pytest.raises(KdevError, match="Could not find"):
        session.cancel_created(CREDS, SLUG, "v12", timeout=4)

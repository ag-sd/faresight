"""Tests for the categorizer subprocess supervisor in app/faresight.py."""
import asyncio
from contextlib import suppress

import pytest

import app.faresight as fs


class _Proc:
    """Fake Popen whose poll() always returns the given exit code (None = alive)."""
    def __init__(self, code=None):
        self.code = code

    def poll(self):
        return self.code


class _ScriptedProc:
    """Fake Popen returning a scripted sequence of poll() results; the last value repeats."""
    def __init__(self, polls):
        self._polls = list(polls)

    def poll(self):
        if len(self._polls) > 1:
            return self._polls.pop(0)
        return self._polls[0]


@pytest.fixture(autouse=True)
def restore_cat_proc():
    original = getattr(fs.app.state, "cat_proc", None)
    yield
    fs.app.state.cat_proc = original


def test_healthy_child_never_respawned(monkeypatch):
    proc = _Proc(None)
    fs.app.state.cat_proc = proc
    monkeypatch.setattr(fs, "_spawn_categorizer",
                        lambda: pytest.fail("respawned a healthy child"))

    async def run():
        task = asyncio.create_task(fs._supervise_categorizer(fs.app, interval=0.001))
        await asyncio.sleep(0.05)  # several poll cycles
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert fs.app.state.cat_proc is proc


def test_dead_child_respawned_and_state_updated(monkeypatch):
    fs.app.state.cat_proc = _Proc(1)
    new_proc = _Proc(None)

    async def run():
        spawned = asyncio.Event()

        def spawn():
            spawned.set()
            return new_proc

        monkeypatch.setattr(fs, "_spawn_categorizer", spawn)
        task = asyncio.create_task(
            fs._supervise_categorizer(fs.app, interval=0.001, backoff_initial=0.001)
        )
        await asyncio.wait_for(spawned.wait(), timeout=2)
        # Let the respawn assignment land before cancelling.
        await asyncio.sleep(0.01)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert fs.app.state.cat_proc is new_proc


def test_backoff_doubles_and_caps(monkeypatch):
    """Consecutive crashes back off 1, 2, 4, 4... (capped); poll interval is distinct."""
    fs.app.state.cat_proc = _Proc(1)
    real_sleep = asyncio.sleep
    sleeps = []
    spawns = {"n": 0}

    def spawn():
        spawns["n"] += 1
        return _Proc(1)  # dies again immediately

    monkeypatch.setattr(fs, "_spawn_categorizer", spawn)

    async def run():
        async def recording_sleep(t):
            sleeps.append(t)
            await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", recording_sleep)
        task = asyncio.create_task(fs._supervise_categorizer(
            fs.app, interval=0.5, backoff_initial=1.0, backoff_max=4.0
        ))
        while spawns["n"] < 5:
            await real_sleep(0)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    backoffs = [s for s in sleeps if s != 0.5]
    assert backoffs[:5] == [1.0, 2.0, 4.0, 4.0, 4.0]


def test_backoff_resets_after_healthy_poll(monkeypatch):
    """A respawned child seen healthy resets the backoff for the next crash."""
    fs.app.state.cat_proc = _Proc(1)
    real_sleep = asyncio.sleep
    sleeps = []
    spawns = {"n": 0}

    def spawn():
        spawns["n"] += 1
        # Healthy for two polls (resets backoff), then dead.
        return _ScriptedProc([None, None, 1])

    monkeypatch.setattr(fs, "_spawn_categorizer", spawn)

    async def run():
        async def recording_sleep(t):
            sleeps.append(t)
            await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", recording_sleep)
        task = asyncio.create_task(fs._supervise_categorizer(
            fs.app, interval=0.5, backoff_initial=1.0, backoff_max=4.0
        ))
        while spawns["n"] < 2:
            await real_sleep(0)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    backoffs = [s for s in sleeps if s != 0.5]
    # First crash backs off 1.0; healthy polls reset; second crash backs off 1.0 again.
    assert backoffs[:2] == [1.0, 1.0]


def test_no_respawn_after_cancel(monkeypatch):
    proc = _Proc(None)
    fs.app.state.cat_proc = proc
    spawns = {"n": 0}
    monkeypatch.setattr(fs, "_spawn_categorizer",
                        lambda: spawns.__setitem__("n", spawns["n"] + 1) or _Proc(None))

    async def run():
        task = asyncio.create_task(fs._supervise_categorizer(fs.app, interval=0.001))
        await asyncio.sleep(0.01)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        # Child dies after the supervisor is gone: nothing may respawn it.
        proc.code = 1
        await asyncio.sleep(0.02)

    asyncio.run(run())
    assert spawns["n"] == 0
    assert fs.app.state.cat_proc is proc


def test_lifespan_spawns_once_and_cancels_supervisor_before_terminate(monkeypatch):
    from fastapi.testclient import TestClient

    events = []

    class _RecordingProc:
        def poll(self):
            return None

        def terminate(self):
            events.append("terminate")

        def wait(self, timeout=None):
            events.append("wait")

        def kill(self):
            events.append("kill")

    spawns = []

    def spawn():
        spawns.append(1)
        return _RecordingProc()

    monkeypatch.setattr(fs, "_spawn_categorizer", spawn)
    with TestClient(fs.app):
        assert fs.app.state.cat_supervisor is not None
        assert not fs.app.state.cat_supervisor.done()

    assert len(spawns) == 1  # supervisor never respawned a healthy child
    assert events == ["terminate", "wait"]  # graceful, no kill()
    assert fs.app.state.cat_supervisor.cancelled()

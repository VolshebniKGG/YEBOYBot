import pytest
from discord import LoginFailure
from core_v2.recovery import recover


@pytest.mark.asyncio
async def test_bounded_retries_and_backoff():
    calls, waits, beats = [], [], []
    async def fail():
        calls.append(1)
        raise OSError("offline")
    async def sleep(seconds):
        waits.append(seconds)
    with pytest.raises(OSError):
        await recover(fail, lambda: False, lambda *args: beats.append(args), (2, 3), sleep)
    assert len(calls) == 3
    assert waits == [1] * 5
    assert len(beats) == 5


@pytest.mark.asyncio
async def test_stop_during_backoff_prevents_restart():
    calls = []
    stopped = False
    async def fail():
        calls.append(1)
        raise OSError()
    async def sleep(_):
        nonlocal stopped
        stopped = True
    await recover(fail, lambda: stopped, lambda *_: None, (5,), sleep)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [LoginFailure("invalid"), ValueError("config")])
async def test_fatal_error_not_retried(error):
    calls = []
    async def fail():
        calls.append(1)
        raise error
    with pytest.raises(type(error)):
        await recover(fail, lambda: False, lambda *_: None)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_successful_recovery_closes_previous_attempt_first():
    active = 0
    calls = 0
    async def operation():
        nonlocal active, calls
        active += 1
        calls += 1
        assert active == 1
        try:
            if calls == 1:
                raise OSError()
        finally:
            active -= 1
    async def sleep(_):
        pass
    await recover(operation, lambda: False, lambda *_: None, (1,), sleep)
    assert calls == 2
    assert active == 0

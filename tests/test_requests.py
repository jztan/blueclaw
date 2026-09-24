import asyncio

import pytest

from blueclaw.cancellation import CancellationControl
from blueclaw.requests import ActiveRequest, RequestRegistry


@pytest.mark.asyncio
async def test_detach_unblocks_full_queue():
    active = ActiveRequest("r", CancellationControl())
    for _ in range(active.queue.maxsize):
        active.queue.put_nowait({"type": "delta", "text": "x"})
    blocked = asyncio.create_task(active.publish({"type": "delta", "text": "y"}))
    await asyncio.sleep(0)
    assert not blocked.done()
    active.detached.set()
    await asyncio.wait_for(blocked, 1)


@pytest.mark.asyncio
async def test_registry_rejects_duplicate_without_replacing_owner():
    registry = RequestRegistry()
    first = registry.register("r")
    with pytest.raises(ValueError):
        registry.register("r")
    assert registry.get("r") is first
    registry.finish("r")
    assert registry.get("r") is None


@pytest.mark.asyncio
async def test_shutdown_signals_and_joins_owned_tasks():
    registry = RequestRegistry()
    active = registry.register("r")
    finished = asyncio.Event()

    async def owned_run():
        while not active.cancellation.event.is_set():
            await asyncio.sleep(0.001)
        finished.set()

    active.task = asyncio.create_task(owned_run())
    await asyncio.wait_for(registry.shutdown(), 1)
    assert finished.is_set()
    assert active.detached.is_set()

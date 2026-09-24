"""App-local ownership of in-flight HTTP requests and SSE backpressure."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from blueclaw.cancellation import CancellationControl


@dataclass
class ActiveRequest:
    request_id: str
    cancellation: CancellationControl
    queue: asyncio.Queue[dict] = field(default_factory=lambda: asyncio.Queue(128))
    detached: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    admitted: bool = False
    streaming: bool = False

    async def publish(self, event: dict) -> None:
        if self.detached.is_set():
            return
        put = asyncio.create_task(self.queue.put(event))
        detached = asyncio.create_task(self.detached.wait())
        try:
            done, pending = await asyncio.wait(
                {put, detached}, return_when=asyncio.FIRST_COMPLETED
            )
            if detached in done and put not in done:
                put.cancel()
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            for task in (put, detached):
                if not task.done():
                    task.cancel()
            await asyncio.gather(put, detached, return_exceptions=True)


class RequestRegistry:
    def __init__(self) -> None:
        self._active: dict[str, ActiveRequest] = {}

    def register(self, request_id: str) -> ActiveRequest:
        if request_id in self._active:
            raise ValueError("duplicate active request_id")
        active = ActiveRequest(request_id, CancellationControl())
        self._active[request_id] = active
        return active

    def get(self, request_id: str) -> ActiveRequest | None:
        return self._active.get(request_id)

    def finish(self, request_id: str) -> None:
        self._active.pop(request_id, None)

    async def shutdown(self) -> None:
        active = list(self._active.values())
        for item in active:
            item.cancellation.request_stop("shutdown")
            item.detached.set()
        tasks = [item.task for item in active if item.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

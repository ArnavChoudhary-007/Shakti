from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable, Optional, Set, TypeVar


Result = TypeVar("Result")


class InferenceScheduler:
    def __init__(self) -> None:
        self._chats = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._idle: asyncio.Event
        self._background_lock: asyncio.Lock
        self._active: Optional[asyncio.Task] = None
        self._interrupted: Set[asyncio.Task] = set()

    @property
    def chatting(self) -> bool:
        return self._chats > 0

    def _bind(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            if self.chatting or self._active is not None:
                raise RuntimeError("Inference scheduling cannot span active event loops.")
            self._loop = loop
            self._idle = asyncio.Event()
            self._idle.set()
            self._background_lock = asyncio.Lock()

    @asynccontextmanager
    async def chat(self) -> AsyncIterator[None]:
        self._bind()
        self._chats += 1
        self._idle.clear()
        active = self._active
        try:
            if active is not None and not active.done():
                if active not in self._interrupted:
                    self._interrupted.add(active)
                    active.cancel()
                await asyncio.gather(active, return_exceptions=True)
            yield
        finally:
            self._chats -= 1
            if not self._chats:
                self._idle.set()

    async def background(self, operation: Callable[[], Awaitable[Result]]) -> Result:
        self._bind()
        async def invoke() -> Result:
            return await operation()

        async with self._background_lock:
            while True:
                await self._idle.wait()
                task = asyncio.create_task(invoke())
                self._active = task
                try:
                    await asyncio.wait({task})
                    if task.cancelled() and task in self._interrupted:
                        continue
                    return task.result()
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    self._interrupted.discard(task)
                    self._active = None

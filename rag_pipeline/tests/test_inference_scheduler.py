import asyncio

import pytest

from rag_pipeline.core.inference_scheduler import InferenceScheduler


def run(scenario):
    return asyncio.run(asyncio.wait_for(scenario(), timeout=3))


def test_chat_interrupts_active_graph_call_and_retries_afterwards():
    async def scenario():
        scheduler = InferenceScheduler()
        started = asyncio.Event()
        cleaned = asyncio.Event()
        attempts = []

        async def operation():
            attempts.append("call")
            if len(attempts) == 1:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()
            return "relationships"

        graph = asyncio.create_task(scheduler.background(operation))
        await started.wait()
        async with scheduler.chat():
            assert cleaned.is_set()
            assert scheduler.chatting
            await asyncio.sleep(0)
            assert attempts == ["call"]
            assert not graph.done()
        assert await graph == "relationships"
        assert len(attempts) == 2
        assert not scheduler.chatting

    run(scenario)


def test_graph_waits_for_all_overlapping_chats():
    async def scenario():
        scheduler = InferenceScheduler()
        first_entered, second_entered = asyncio.Event(), asyncio.Event()
        release_first, release_second = asyncio.Event(), asyncio.Event()
        graph_started = asyncio.Event()

        async def chat(entered, release):
            async with scheduler.chat():
                entered.set()
                await release.wait()

        async def operation():
            graph_started.set()
            return 42

        first = asyncio.create_task(chat(first_entered, release_first))
        await first_entered.wait()
        second = asyncio.create_task(chat(second_entered, release_second))
        await second_entered.wait()
        graph = asyncio.create_task(scheduler.background(operation))
        release_first.set()
        await first
        await asyncio.sleep(0)
        assert scheduler.chatting
        assert not graph_started.is_set()
        release_second.set()
        await second
        assert await graph == 42

    run(scenario)


def test_preemption_before_the_graph_coroutine_starts_is_retryable():
    async def scenario():
        scheduler = InferenceScheduler()
        entered, release = asyncio.Event(), asyncio.Event()
        attempts = []

        async def operation():
            attempts.append("call")
            return "done"

        async def chat():
            async with scheduler.chat():
                entered.set()
                await release.wait()

        graph = asyncio.create_task(scheduler.background(operation))
        question = asyncio.create_task(chat())
        await entered.wait()
        assert attempts == []
        release.set()
        await question
        assert await graph == "done"
        assert attempts == ["call"]

    run(scenario)


@pytest.mark.parametrize("waiting_for_chat", [False, True])
def test_cancelling_a_build_does_not_retry_it_or_block_future_work(waiting_for_chat):
    async def scenario():
        scheduler = InferenceScheduler()
        started, cleaned = asyncio.Event(), asyncio.Event()

        async def operation():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        if waiting_for_chat:
            async with scheduler.chat():
                graph = asyncio.create_task(scheduler.background(operation))
                await asyncio.sleep(0)
                graph.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await graph
            assert not started.is_set()
        else:
            graph = asyncio.create_task(scheduler.background(operation))
            await started.wait()
            graph.cancel()
            with pytest.raises(asyncio.CancelledError):
                await graph
            assert cleaned.is_set()
        assert await scheduler.background(lambda: asyncio.sleep(0, result="next")) == "next"

    run(scenario)


def test_chat_failure_and_cancellation_release_the_pause():
    async def scenario():
        scheduler = InferenceScheduler()
        with pytest.raises(ValueError):
            async with scheduler.chat():
                raise ValueError("chat failed")
        assert not scheduler.chatting
        entered = asyncio.Event()

        async def question():
            async with scheduler.chat():
                entered.set()
                await asyncio.Event().wait()

        chat = asyncio.create_task(question())
        await entered.wait()
        chat.cancel()
        with pytest.raises(asyncio.CancelledError):
            await chat
        assert not scheduler.chatting
        assert await scheduler.background(lambda: asyncio.sleep(0, result=42)) == 42

    run(scenario)


def test_graph_requests_are_serialized_across_workspaces():
    async def scenario():
        scheduler = InferenceScheduler()
        first_started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def operation(name):
            calls.append(name)
            if name == "first":
                first_started.set()
                await release.wait()
            return name

        first = asyncio.create_task(scheduler.background(lambda: operation("first")))
        await first_started.wait()
        second = asyncio.create_task(scheduler.background(lambda: operation("second")))
        await asyncio.sleep(0)
        assert calls == ["first"]
        release.set()
        assert await asyncio.gather(first, second) == ["first", "second"]

    run(scenario)


def test_real_graph_errors_are_not_retried_automatically():
    async def scenario():
        scheduler = InferenceScheduler()
        attempts = []

        async def operation():
            attempts.append("call")
            raise ValueError("invalid graph output")

        with pytest.raises(ValueError, match="invalid graph output"):
            await scheduler.background(operation)
        assert attempts == ["call"]

    run(scenario)

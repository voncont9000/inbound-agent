import asyncio
from contextlib import suppress
from types import SimpleNamespace

from pipecat.frames.frames import EndWorkerFrame, TTSSpeakFrame

from inbound_voice.call_settings import MaxCallDurationObserver, wait_for_max_call_duration


def test_max_call_duration_speaks_then_ends_call() -> None:
    async def run() -> None:
        queued_frames = []
        markers = []
        tasks = []

        async def queue_frame(frame) -> None:
            queued_frames.append(frame)

        async def wait_for_marker(marker_id, *, timeout) -> None:
            markers.append((marker_id, timeout))

        await wait_for_max_call_duration(
            0,
            "Configured maximum duration message",
            queue_frame=queue_frame,
            new_marker=lambda: "marker-1",
            marker_frame=lambda marker_id: ("speech-marker", marker_id),
            wait_for_marker=wait_for_marker,
            call_ended=lambda: False,
            transfer_in_progress=lambda: False,
            append_task=lambda kind, fields: tasks.append((kind, fields)),
        )

        assert isinstance(queued_frames[0], TTSSpeakFrame)
        assert queued_frames[0].text == "Configured maximum duration message"
        assert queued_frames[1] == ("speech-marker", "marker-1")
        assert isinstance(queued_frames[2], EndWorkerFrame)
        assert markers == [("marker-1", 15.0)]
        assert tasks == [("end_call", {"source": "max_duration"})]

    asyncio.run(run())


def test_max_call_duration_skips_ended_calls_and_transfers() -> None:
    async def run() -> None:
        queued = []
        tasks = []
        for ended, transferring in ((True, False), (False, True)):
            await wait_for_max_call_duration(
                0,
                "message",
                queue_frame=lambda frame: queued.append(frame),
                new_marker=lambda: "marker",
                marker_frame=lambda marker_id: marker_id,
                wait_for_marker=lambda marker_id, *, timeout: None,
                call_ended=lambda ended=ended: ended,
                transfer_in_progress=lambda transferring=transferring: transferring,
                append_task=lambda kind, fields: tasks.append((kind, fields)),
            )
        assert queued == []
        assert tasks == []

    asyncio.run(run())


def test_max_call_observer_does_not_start_without_a_limit() -> None:
    observer = MaxCallDurationObserver(
        None,
        "message",
        queue_frame=lambda frame: None,
        speech_tracker=SimpleNamespace(),
        marker_frame=lambda marker_id: marker_id,
        call_tools=SimpleNamespace(end_call_requested=False, transfer_request=None),
        sink=SimpleNamespace(append_task=lambda *args: None),
    )
    observer.start()
    assert observer.pending is None


def test_max_call_observer_start_is_idempotent() -> None:
    async def run() -> None:
        async def queue_frame(frame) -> None:
            return None

        async def wait_for_marker(marker_id, *, timeout) -> None:
            return None

        observer = MaxCallDurationObserver(
            60,
            "message",
            queue_frame=queue_frame,
            speech_tracker=SimpleNamespace(
                new_marker=lambda: "marker",
                wait_for_marker=wait_for_marker,
            ),
            marker_frame=lambda marker_id: marker_id,
            call_tools=SimpleNamespace(end_call_requested=False, transfer_request=None),
            sink=SimpleNamespace(append_task=lambda *args: None),
        )
        tasks_before = len(asyncio.all_tasks())

        observer.start()
        first = observer.pending
        assert first is not None
        observer.start()

        assert observer.pending is first
        assert len(asyncio.all_tasks()) - tasks_before == 1

        observer.close()
        assert observer.pending is None
        with suppress(asyncio.CancelledError):
            await first
        assert first.cancelled() or first.done()
        assert not [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_coro().__qualname__ == "wait_for_max_call_duration"
        ]

    asyncio.run(run())

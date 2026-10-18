import asyncio
import logging
import signal

from inbound_api.jobs import worker
from inbound_api.services import job_handlers as _job_handlers  # noqa: F401


async def _run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    await worker(stop=stop)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


if __name__ == "__main__":
    main()

"""Run the Redis Streams worker: `python scripts/worker.py`."""
import asyncio

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.task_queue import run_worker


if __name__ == "__main__":
    configure_logging()
    asyncio.run(run_worker(get_settings()))

"""`uv run worker` (or `python -m worker`): run the poll loop until SIGTERM or Ctrl-C."""

from __future__ import annotations

import logging
import signal
import sys

from worker.config import Config, ConfigError
from worker.entrypoints.poll_loop import PollLoop
from worker.wiring import build

log = logging.getLogger("worker")


def main() -> int:
    try:
        config = Config.from_env()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s",
    )
    app = build(config)
    loop = PollLoop(
        app.queue,
        app.deps,
        app.state,
        concurrency=config.concurrency,
        visibility_seconds=config.visibility_seconds,
    )

    def stop(signum: int, _frame: object) -> None:
        log.info("%s received: finishing runs in progress", signal.Signals(signum).name)
        loop.stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    loop.run_forever()
    loop.drain()
    app.tracer.shutdown()  # sends the spans still waiting (scale-in sends SIGTERM)
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

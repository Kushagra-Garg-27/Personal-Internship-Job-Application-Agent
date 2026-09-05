"""Browser Automation Worker standalone entry point (Phase 9).

Separate long-running process from Core. Reads approved jobs from shared DB,
fills applications via tiered adapters, and stops before submit.
"""

from __future__ import annotations

import argparse
import logging
import sys

from worker.runner import WorkerRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("worker")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Browser Automation Worker (Phase 9) — Fills applications and gates on human submission."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single queue polling pass and exit.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=20,
        help="Polling interval in seconds for daemon mode (default: 20s).",
    )
    args = parser.parse_args()

    runner = WorkerRunner(poll_interval=args.interval)

    if args.once:
        logger.info("Running single worker pass...")
        results = runner.run_once()
        logger.info("Completed single pass. Processed: %d items", len(results))
        sys.exit(0)

    logger.info("Starting Browser Automation Worker daemon...")
    runner.start_blocking()


if __name__ == "__main__":
    main()

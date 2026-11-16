"""Entry point: ``python -m transit_lakehouse.collector``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from transit_lakehouse.collector.config import ConfigError, load_settings
from transit_lakehouse.collector.poller import Collector


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Print the feed plan and quota projection, then exit.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # httpx logs every request at INFO; the manifest already records them.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = load_settings()
        settings.check_quota_budget()
    except ConfigError as exc:
        logging.error("%s", exc)
        return 2

    if args.check_config:
        for feed in settings.feeds:
            print(
                f"{feed.name:<32} every {feed.interval_s:>4.0f}s "
                f"{feed.calls_per_day:>7,.0f}/day  {feed.url}"
            )
        share = settings.projected_calls_per_day / settings.daily_quota
        print(
            f"Projected {settings.projected_calls_per_day:,.0f} calls/day "
            f"= {share:.0%} of {settings.daily_quota:,} quota"
        )
        return 0

    collector = Collector(settings)

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, collector.stop)
        await collector.run()

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    sys.exit(main())

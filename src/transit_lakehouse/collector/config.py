"""Collector configuration: feed list from TOML, secrets from env."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

SECONDS_PER_DAY = 86_400
DEFAULT_DAILY_QUOTA = 60_000
DEFAULT_BUDGET_FRACTION = 0.8
DEFAULT_MAX_RPS = 4.0
VALID_KINDS = frozenset({"vehicle_positions", "trip_updates"})


class ConfigError(ValueError):
    """Raised when configuration is missing or inconsistent."""


@dataclass(frozen=True)
class Feed:
    """One GTFS-realtime endpoint and how often to poll it."""

    name: str
    kind: str
    url: str
    interval_s: float

    @property
    def calls_per_day(self) -> float:
        return SECONDS_PER_DAY / self.interval_s


@dataclass(frozen=True)
class Settings:
    """Runtime settings for one collector process."""

    api_key: str
    data_dir: Path
    feeds: tuple[Feed, ...]
    daily_quota: int = DEFAULT_DAILY_QUOTA
    budget_fraction: float = DEFAULT_BUDGET_FRACTION
    max_rps: float = DEFAULT_MAX_RPS
    stagger_s: float = 1.5
    timeout_s: float = 10.0

    @property
    def projected_calls_per_day(self) -> float:
        return sum(f.calls_per_day for f in self.feeds)

    def check_quota_budget(self) -> None:
        """Refuse a feed set that would exceed the quota budget."""
        budget = self.daily_quota * self.budget_fraction
        projected = self.projected_calls_per_day
        if projected > budget:
            raise ConfigError(
                f"Enabled feeds need {projected:,.0f} calls/day but the "
                f"budget is {budget:,.0f} ({self.budget_fraction:.0%} of "
                f"{self.daily_quota:,}). Disable feeds, slow intervals, "
                "or raise QUOTA_BUDGET_FRACTION deliberately."
            )


def load_feeds(path: Path) -> tuple[Feed, ...]:
    """Read enabled feeds from a TOML file."""
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    defaults = raw.get("defaults", {})
    base_url = defaults.get(
        "base_url", "https://api.transport.nsw.gov.au"
    ).rstrip("/")
    intervals = {
        "vehicle_positions": defaults.get("vehicle_positions_interval_s", 15),
        "trip_updates": defaults.get("trip_updates_interval_s", 30),
    }
    feeds: list[Feed] = []
    seen: set[str] = set()
    for entry in raw.get("feeds", []):
        if not entry.get("enabled", True):
            continue
        name, kind = entry["name"], entry["kind"]
        if kind not in VALID_KINDS:
            raise ConfigError(f"Feed {name}: unknown kind {kind!r}")
        if name in seen:
            raise ConfigError(f"Duplicate feed name {name!r}")
        seen.add(name)
        interval = float(entry.get("interval_s", intervals[kind]))
        if interval < 10:
            # TfNSW refreshes every 10-15 s; faster polls only burn quota.
            raise ConfigError(f"Feed {name}: interval below 10 s")
        feeds.append(
            Feed(
                name=name,
                kind=kind,
                url=base_url + entry["path"],
                interval_s=interval,
            )
        )
    if not feeds:
        raise ConfigError(f"No enabled feeds in {path}")
    return tuple(feeds)


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build settings from environment variables."""
    env = dict(os.environ) if env is None else env
    api_key = env.get("TFNSW_API_KEY", "").strip()
    if not api_key or api_key.startswith("replace-me"):
        raise ConfigError(
            "TFNSW_API_KEY is not set. Copy .env.example to .env and "
            "paste your Open Data Hub key (see docs/5_api_token_setup.md)."
        )
    feeds_path = Path(env.get("FEEDS_CONFIG", "config/feeds.toml"))
    return Settings(
        api_key=api_key,
        data_dir=Path(env.get("DATA_DIR", "data")),
        feeds=load_feeds(feeds_path),
        daily_quota=int(env.get("DAILY_QUOTA", DEFAULT_DAILY_QUOTA)),
        budget_fraction=float(
            env.get("QUOTA_BUDGET_FRACTION", DEFAULT_BUDGET_FRACTION)
        ),
        max_rps=float(env.get("MAX_RPS", DEFAULT_MAX_RPS)),
    )

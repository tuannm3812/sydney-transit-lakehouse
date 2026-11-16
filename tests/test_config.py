from __future__ import annotations

from pathlib import Path

import pytest

from transit_lakehouse.collector.config import (
    ConfigError,
    Feed,
    Settings,
    load_feeds,
    load_settings,
)

REPO = Path(__file__).resolve().parents[1]


def test_repo_feed_config_fits_quota_budget() -> None:
    feeds = load_feeds(REPO / "config" / "feeds.toml")
    settings = Settings(api_key="k", data_dir=Path("d"), feeds=feeds)
    settings.check_quota_budget()  # must not raise
    kinds = {f.kind: f.interval_s for f in feeds if "buses" in f.name}
    assert kinds == {"vehicle_positions": 15, "trip_updates": 30}


def test_budget_guard_rejects_overspend() -> None:
    feeds = tuple(
        Feed(f"f{i}", "vehicle_positions", "https://x", 15)
        for i in range(11)  # 11 x 5,760 = 63,360 > 60,000
    )
    with pytest.raises(ConfigError, match="calls/day"):
        Settings(
            api_key="k", data_dir=Path("d"), feeds=feeds
        ).check_quota_budget()


def test_missing_key_is_a_clear_error() -> None:
    with pytest.raises(ConfigError, match="TFNSW_API_KEY"):
        load_settings({"TFNSW_API_KEY": "replace-me"})


def test_interval_floor(tmp_path: Path) -> None:
    cfg = tmp_path / "feeds.toml"
    cfg.write_text(
        '[[feeds]]\nname="a"\nkind="trip_updates"\npath="/x"\ninterval_s=5\n'
    )
    with pytest.raises(ConfigError, match="below 10"):
        load_feeds(cfg)

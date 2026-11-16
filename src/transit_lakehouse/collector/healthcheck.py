"""Container healthcheck: is the heartbeat recent?"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

MAX_AGE_S = 120


def main() -> int:
    path = Path(os.environ.get("DATA_DIR", "data")) / "_heartbeat"
    try:
        beat = datetime.fromisoformat(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 1
    age = (datetime.now(UTC) - beat).total_seconds()
    return 0 if age <= MAX_AGE_S else 1


if __name__ == "__main__":
    sys.exit(main())

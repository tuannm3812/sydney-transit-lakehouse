"""Dispatch: ``python -m transit_lakehouse.checks capture|quota ...``."""

from __future__ import annotations

import sys

from transit_lakehouse.checks import capture, quota_report

COMMANDS = {"capture": capture.main, "quota": quota_report.main}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in COMMANDS:
        print("usage: python -m transit_lakehouse.checks capture|quota ...")
        return 2
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main())

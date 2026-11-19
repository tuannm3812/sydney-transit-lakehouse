"""Render phase reports to PDF (docs/reports/README.md).

    python -m transit_lakehouse.reports docs/reports/x.md --out build/
    python -m transit_lakehouse.reports docs/reports/x.md --check

Set REPORT_ASSET_DIR to the deck kit's assets folder for the fonts and
logos, and CHROME_BIN if Chrome is not in a usual place.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from transit_lakehouse.reports.render import (
    ReportAssets,
    ReportError,
    parse,
    to_html,
    to_pdf,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=Path("build/reports"))
    parser.add_argument(
        "--check", action="store_true", help="check the standard only"
    )
    parser.add_argument(
        "--html", action="store_true", help="also write the HTML page"
    )
    args = parser.parse_args(argv)
    asset_dir = os.environ.get("REPORT_ASSET_DIR")
    assets = ReportAssets(Path(asset_dir) if asset_dir else None)
    failed = 0
    for src in args.reports:
        try:
            report = parse(src.read_text(encoding="utf-8"))
        except ReportError as exc:
            print(f"{src}: {exc}", file=sys.stderr)
            failed += 1
            continue
        if args.check:
            print(f"{src}: ok ({report.meta['id']})")
            continue
        html = to_html(report, assets)
        stem = f"{report.meta['id']}-{src.stem}"
        if args.html:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / f"{stem}.html").write_text(html, encoding="utf-8")
        pdf = to_pdf(html, args.out / f"{stem}.pdf")
        print(f"{src}: {pdf}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

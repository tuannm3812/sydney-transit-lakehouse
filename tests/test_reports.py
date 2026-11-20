"""Report standard checks (docs/reports/README.md)."""

from pathlib import Path

import pytest

from transit_lakehouse.reports.render import (
    ReportError,
    chart_svg,
    parse,
    to_html,
)

FRONT = """---
id: R9-01
title: A test report
subtitle: What it tests.
phase: 1
status: Draft
verdict: Info
date: 2026-11-19
window: 1 Nov 2026 UTC
commit: abc1234
environment: CI
decisions: D-001
evidence: none
---

# A test report
"""
BODY = "".join(
    f"## {s}\n\nText.\n\n"
    for s in (
        "Summary",
        "Scope and method",
        "Results",
        "Findings",
        "Limits",
        "Next steps",
    )
)


def test_valid_report_parses_and_renders():
    body = BODY.replace("## Summary\n\nText.", "## Summary\n\n> Answer.")
    extra = (
        "```chart\ntitle: T\nBus 10:00: 5\nOther: 3\n```\n"
        "```flow\nraw | A | a\nsilver | B | b\n```\n"
        "| Feed | Rows | *Size* | Check |\n|---|---:|---:|---|\n"
        "| `buses_vehiclepos` | **5** | 1 MB | Pass |\n"
        "| `metro_vehiclepos` | 4 | 9 MB | Pass |\n"
        "| All feeds | 9 | 10 MB | |\n"
    )
    rep = parse(FRONT + body + extra)
    assert rep.meta["id"] == "R9-01"
    assert "# A test report" not in rep.body
    html = to_html(rep)
    assert 'class="answer"' in html
    assert "<svg" in html and "Bus 10:00" in html
    assert "#00B5EF" in html and "#9AA3AD" in html  # mode bar, neutral bar
    assert 'class="flow"' in html and "step silver" in html
    assert 'class="chip st-good">Pass' in html
    assert 'class="total"' in html and 'class="hl"' in html
    assert 'class="feed"' in html
    assert 'class="scale"' in html
    assert 'class="s0"' in html and 'class="s4"' in html
    assert html.count(' s4"') + html.count('"s4"') == 1  # total not scaled
    assert "Build the lakehouse" in html  # phase track


@pytest.mark.parametrize(
    "change, message",
    [
        (("verdict: Info\n", ""), "missing verdict"),
        (("status: Draft", "status: Done"), "status must be"),
        (("title: A test report", "title: " + "word " * 9), "8 words"),
        (("subtitle: What it tests.", "subtitle: No full stop"), "subtitle"),
        (("evidence: none", "evidence: none\nextra: x"), "unknown"),
        (("## Limits", "## Caveats"), "sections must be"),
        (("Text.", "Host 10.1.2.3."), "IP address"),
        (("Text.", "Mail a@b.com."), "email"),
    ],
)
def test_breaking_the_standard_is_refused(change, message):
    text = (FRONT + BODY).replace(*change, 1)
    with pytest.raises(ReportError, match=message.split()[-1]):
        parse(text)


def test_leak_error_does_not_echo_the_secret():
    secret = "A" * 40
    with pytest.raises(ReportError) as err:
        parse((FRONT + BODY).replace("Text.", secret, 1))
    assert secret[:8] not in str(err.value)


def test_chart_draws_limit_line():
    svg = chart_svg("title: M\nunit: MB\nlimit: 900 | cap\nRun: 757\n")
    assert 'class="lim"' in svg and "757 MB" in svg


def test_reports_in_repo_follow_the_standard():
    root = Path(__file__).parents[1] / "docs" / "reports"
    for path in root.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        if text.startswith("---\n"):
            parse(text)

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
subtitle: What it tests
phase: 1
status: Draft
verdict: Info
date: 2026-11-19
commit: abc1234
environment: CI
decisions: D-001
evidence: none
highlights:
  - 1 | one
  - 2 | two
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
    rep = parse(FRONT + body + "```chart\ntitle: T\nA 10:00: 5\n```\n")
    assert rep.meta["id"] == "R9-01"
    assert rep.highlights == [("1", "one"), ("2", "two")]
    assert "# A test report" not in rep.body
    html = to_html(rep)
    assert 'class="answer"' in html
    assert "<svg" in html and "A 10:00" in html


@pytest.mark.parametrize(
    "change, message",
    [
        (("verdict: Info\n", ""), "missing verdict"),
        (("status: Draft", "status: Done"), "status must be"),
        (("  - 2 | two\n", ""), "two to four"),
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

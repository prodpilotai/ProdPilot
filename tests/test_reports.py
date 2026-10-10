"""Markdown and HTML reports: same findings as SARIF, escaped, self-contained, secret-free."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prodpilot import builds
from prodpilot.cli import app
from prodpilot.formats import Decision, decide, to_html, to_markdown
from prodpilot.reporting import Location, build, mask
from prodpilot.sarif import to_sarif

from test_sarif import KEY, plant

SAMPLES = Path(__file__).resolve().parent / "samples"
SHUT = Decision(False, "score 13 but 6 critical rule(s) still fail")


@pytest.fixture
def built(monkeypatch):
    """The gate's build question answered without Docker, as the baseline does."""
    def cached(root, stack, run=None):
        return builds.Build(builds.BUILT, reason="fixed by the test")
    monkeypatch.setattr(builds, "cached", cached)


def md_rules(text: str) -> list[tuple[str, str]]:
    rows = re.findall(r"^\| ([A-Z]{3}-\d{3}) \|.*?\| (<code>.*?) \| [A-Z-]+ \|$", text, re.M)
    return [(rule, re.findall(r"<code>(.*?)</code>", where)[0]) for rule, where in rows]


def html_rules(text: str) -> list[tuple[str, str]]:
    rows = re.findall(r'<tr data-rule="([A-Z]{3}-\d{3})">(.*?)</tr>', text)
    return [(rule, re.findall(r"<code>(.*?)</code>", body)[0]) for rule, body in rows]


def sarif_rules(log: dict) -> list[tuple[str, str]]:
    out = []
    for r in log["runs"][0]["results"]:
        where = r["locations"][0]["physicalLocation"]
        line = where.get("region", {}).get("startLine")
        uri = where["artifactLocation"]["uri"]
        out.append((r["ruleId"], f"{uri}:{line}" if line else uri))
    return out


@pytest.mark.parametrize("sample", ["node_express_insecure", "react_vite_app",
                                    "node_express_secrets", "amb_two_lockfiles"])
def test_the_three_formats_report_the_same_findings(sample: str) -> None:
    view = build(SAMPLES / sample)
    expected = sarif_rules(to_sarif(view))
    assert sorted(md_rules(to_markdown(view, SHUT))) == sorted(expected)
    assert sorted(html_rules(to_html(view, SHUT))) == sorted(expected)
    assert len(expected) == len(view.report.issues)


def test_the_markdown_report_carries_every_section() -> None:
    view = build(SAMPLES / "node_express_insecure")
    text = to_markdown(view, SHUT)
    assert text.startswith("# ProdPilot report: node_express_insecure\n")
    assert f"**Score:** {view.report.score}/100, {view.report.band.value}" in text
    assert "**Gate:** not ready. score 13 but 6 critical rule(s) still fail" in text
    assert "## Failing rules by priority" in text and "## Failing rules by domain" in text
    headings = re.findall(r"^### (P\d) \((\d+)\)$", text, re.M)
    assert [h for h, _ in headings] == sorted(h for h, _ in headings)
    assert sum(int(n) for _, n in headings) == len(view.items)
    for item in view.items:
        assert item.result.fix_type.value in text


def test_markdown_table_cells_cannot_break_the_table() -> None:
    view = build(SAMPLES / "node_express_insecure")
    item = replace(view.items[0], message="a | b\nc `d` <e>")
    text = to_markdown(replace(view, items=(item,)), SHUT)
    row = next(line for line in text.splitlines() if line.startswith(f"| {item.rule_id} |"))
    assert "a \\| b c `d` &lt;e&gt;" in row


def test_the_html_report_is_self_contained() -> None:
    text = to_html(build(SAMPLES / "react_vite_app"), SHUT)
    assert text.startswith("<!doctype html>")
    assert "<script" not in text.lower()
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", text, re.I)
    assert "@import" not in text and "url(" not in text
    assert "<link" not in text.lower() and "<img" not in text.lower()
    assert "prefers-color-scheme: dark" in text


def test_the_html_report_escapes_a_hostile_path() -> None:
    view = build(SAMPLES / "node_express_insecure")
    hostile = '<script>alert("x")</script>.js'
    item = replace(view.items[0], locations=(Location(f"src/{hostile}", 3),),
                   message=f'bad "{hostile}"')
    text = to_html(replace(view, items=(item,)), SHUT)
    assert "<script>" not in text
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;.js" in text
    assert "&lt;script&gt;" in to_markdown(replace(view, items=(item,)), SHUT)


def test_notices_appear_in_both_reports(tmp_path: Path) -> None:
    root = tmp_path / "app"
    shutil.copytree(SAMPLES / "node_express_ready", root)
    (root / ".nvmrc").write_text("18\n", encoding="utf-8")
    view = build(root)
    assert "## Notices" in to_markdown(view, SHUT) and "Node 18" in to_markdown(view, SHUT)
    assert "<h2>Notices</h2>" in to_html(view, SHUT)


def test_a_planted_key_appears_in_no_byte_of_any_report(tmp_path: Path) -> None:
    view = build(plant(tmp_path))
    outputs = [json.dumps(to_sarif(view), ensure_ascii=False), to_markdown(view, SHUT),
               to_html(view, SHUT)]
    for text in outputs:
        data = text.encode("utf-8")
        assert KEY.encode() not in data
        assert (KEY[:6] + "..." + KEY[-4:]).encode() not in data
        assert KEY[4:].encode() not in data
    assert mask(KEY) in outputs[1]
    assert mask(KEY).replace("(", "(") in outputs[2]


def test_the_gate_decision_is_the_gates_own(built) -> None:
    ready = decide(build(SAMPLES / "node_express_gated"))
    assert ready.ready is True and "operating point" in ready.reason
    shut = decide(build(SAMPLES / "node_express_insecure"))
    assert shut.ready is False and "critical rule(s) still fail" in shut.reason
    unknown = decide(build(SAMPLES / "unrecognized_python_service"))
    assert unknown.ready is False and unknown.reason


@pytest.mark.parametrize("fmt,start", [("md", "# ProdPilot report"), ("html", "<!doctype html>")])
def test_the_report_command_writes_each_format(tmp_path: Path, fmt: str, start: str):
    out = tmp_path / f"report.{fmt}"
    done = CliRunner().invoke(app, ["report", "--format", fmt, "--out", str(out),
                                    str(SAMPLES / "react_vite_app")])
    assert done.exit_code == 0, done.output
    assert out.read_text(encoding="utf-8").startswith(start)

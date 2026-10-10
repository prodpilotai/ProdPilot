"""SARIF 2.1.0 export: valid against the official schema, stable, located and secret-free."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from prodpilot.cli import app
from prodpilot.reporting import build, mask
from prodpilot.rules import ALL_RULES
from prodpilot.sarif import FINGERPRINT, to_sarif

HERE = Path(__file__).resolve().parent
SAMPLES = HERE / "samples"
SCHEMA = json.loads((HERE / "fixtures" / "sarif" / "sarif-schema-2.1.0.json")
                    .read_text(encoding="utf-8"))

# Assembled at run time so no credential-shaped literal sits in this file.
KEY = "gh" + "p_" + "Q7mZ2xV9tR" + "4kL8wN3pH6" + "sJ1dF5gC0b" + "Y2aT8u"


def log_of(sample: str | Path) -> dict:
    return to_sarif(build(SAMPLES / sample if isinstance(sample, str) else sample))


def results(log: dict) -> list[dict]:
    return log["runs"][0]["results"]


@pytest.mark.parametrize("sample", [
    "node_express_insecure", "node_express_secrets", "react_vite_app", "node_express_gated",
    "react_vite_ready", "unrecognized_python_service", "amb_two_lockfiles",
])
def test_the_log_validates_against_the_official_schema(sample: str) -> None:
    jsonschema.Draft4Validator(SCHEMA).validate(log_of(sample))


def test_the_driver_describes_all_fifty_rules() -> None:
    driver = log_of("react_vite_app")["runs"][0]["tool"]["driver"]
    assert driver["name"] == "ProdPilot"
    assert driver["informationUri"].startswith("https://")
    rules = driver["rules"]
    assert [r["id"] for r in rules] == [r.rule_id for r in ALL_RULES]
    for described, rule in zip(rules, ALL_RULES):
        assert described["shortDescription"]["text"] == rule.description
        assert described["fullDescription"]["text"]
        assert rule.fix_type.value in described["help"]["text"]
        assert described["properties"]["domain"] == rule.domain.value
        assert described["properties"]["priority"] == rule.priority.value


@pytest.mark.parametrize("priority,level", [
    ("P0", "error"), ("P1", "error"), ("P2", "warning"), ("P3", "warning"),
    ("P4", "note"), ("P5", "note"),
])
def test_levels_follow_priority(priority: str, level: str) -> None:
    driver = log_of("node_express_insecure")["runs"][0]["tool"]["driver"]["rules"]
    for described in driver:
        if described["properties"]["priority"] == priority:
            assert described["defaultConfiguration"]["level"] == level
    for found in results(log_of("node_express_insecure")):
        if found["properties"]["priority"] == priority:
            assert found["level"] == level


def test_every_failing_rule_is_one_result() -> None:
    view = build(SAMPLES / "node_express_insecure")
    found = results(to_sarif(view))
    assert [r["ruleId"] for r in found] == [r.rule_id for r in view.report.issues]
    assert all(r["message"]["text"] for r in found)


def test_paths_are_relative_to_the_repository_root() -> None:
    log = log_of("node_express_insecure")
    base = log["runs"][0]["originalUriBaseIds"]["%SRCROOT%"]["uri"]
    assert base.startswith("file:///") and base.endswith("/")
    for found in results(log):
        for where in found["locations"] + found.get("relatedLocations", []):
            artifact = where["physicalLocation"]["artifactLocation"]
            assert artifact["uriBaseId"] == "%SRCROOT%"
            assert artifact["uri"].startswith("tests/samples/node_express_insecure/")
            assert "\\" not in artifact["uri"] and ":" not in artifact["uri"]


def test_a_line_is_reported_where_the_checker_knows_it() -> None:
    found = {r["ruleId"]: r for r in results(log_of("node_express_insecure"))}
    lined = [r for r in found.values() if "region" in r["locations"][0]["physicalLocation"]]
    assert lined, "at least one code rule points at a line"
    for r in lined:
        assert r["locations"][0]["physicalLocation"]["region"]["startLine"] >= 1


def test_a_project_level_rule_is_attached_to_package_json() -> None:
    attached = [r for r in results(log_of("node_express_insecure"))
                if r["properties"]["attachedToManifest"]]
    assert attached
    for r in attached:
        uri = r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        assert uri.endswith("/package.json")


def test_fingerprints_are_stable_across_runs() -> None:
    first = {r["ruleId"]: r["partialFingerprints"][FINGERPRINT]
             for r in results(log_of("node_express_insecure"))}
    second = {r["ruleId"]: r["partialFingerprints"][FINGERPRINT]
              for r in results(log_of("node_express_insecure"))}
    assert first == second and len(set(first.values())) == len(first)


def test_a_fingerprint_does_not_depend_on_the_line(tmp_path: Path) -> None:
    """Moving the code down a line is the same finding."""
    root = tmp_path / "app"
    shutil.copytree(SAMPLES / "node_express_insecure", root)
    before = {r["ruleId"]: r["partialFingerprints"][FINGERPRINT] for r in results(log_of(root))}
    for js in root.rglob("*.js"):
        js.write_text("\n" + js.read_text(encoding="utf-8"), encoding="utf-8")
    after = {r["ruleId"]: r["partialFingerprints"][FINGERPRINT] for r in results(log_of(root))}
    assert before == after


def test_notices_travel_as_notifications_not_results(tmp_path: Path) -> None:
    root = tmp_path / "app"
    shutil.copytree(SAMPLES / "node_express_ready", root)
    (root / ".nvmrc").write_text("18\n", encoding="utf-8")
    log = log_of(root)
    said = log["runs"][0]["invocations"][0]["toolExecutionNotifications"]
    assert any("Node 18" in n["message"]["text"] and n["level"] == "note" for n in said)
    jsonschema.Draft4Validator(SCHEMA).validate(log)


def plant(tmp_path: Path) -> Path:
    root = tmp_path / "planted"
    shutil.copytree(SAMPLES / "node_express_ready", root)
    (root / "src" / "keys.js").write_text(f'const token = "{KEY}";\nmodule.exports = token;\n',
                                          encoding="utf-8")
    return root


def test_a_planted_key_appears_in_no_byte_of_the_sarif(tmp_path: Path) -> None:
    log = log_of(plant(tmp_path))
    text = json.dumps(log, ensure_ascii=False)
    secret = [r for r in results(log) if r["ruleId"] == "SCR-002"]
    assert secret, "the planted key must be found, or this test proves nothing"
    assert KEY not in text and KEY.encode() not in text.encode("utf-8")
    assert KEY[:6] + "..." + KEY[-4:] not in text
    assert KEY[4:] not in text and KEY[-8:] not in text
    assert mask(KEY) in secret[0]["message"]["text"]
    where = secret[0]["locations"][0]["physicalLocation"]
    assert where["artifactLocation"]["uri"].endswith("src/keys.js")
    assert where["region"]["startLine"] == 1


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------


def test_the_report_command_writes_sarif_and_exits_zero_with_failing_rules(tmp_path: Path):
    out = tmp_path / "audit.sarif"
    done = CliRunner().invoke(app, ["report", "--format", "sarif", "--out", str(out),
                                    str(SAMPLES / "node_express_insecure")])
    assert done.exit_code == 0, done.output
    log = json.loads(out.read_text(encoding="utf-8"))
    jsonschema.Draft4Validator(SCHEMA).validate(log)
    assert results(log)


def test_the_report_command_fails_on_a_missing_project(tmp_path: Path):
    done = CliRunner().invoke(app, ["report", "--format", "sarif", "--out",
                                    str(tmp_path / "x.sarif"), str(tmp_path / "missing")])
    assert done.exit_code == 2
    assert not (tmp_path / "x.sarif").exists()


def test_the_report_command_refuses_an_unknown_format(tmp_path: Path):
    done = CliRunner().invoke(app, ["report", "--format", "pdf", "--out",
                                    str(tmp_path / "x"), str(SAMPLES / "react_vite_app")])
    assert done.exit_code == 2

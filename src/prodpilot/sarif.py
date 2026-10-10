"""SARIF 2.1.0 export of an audit, for GitHub code scanning and other viewers.

The log carries all 50 rules in tool.driver.rules, whether or not they apply to
the project, so a viewer can describe any ProdPilot rule. Each failing rule
becomes one result. Its primary location is the first place the checker found
the problem, and every further place is a related location. Levels follow the
rule's priority: P0 and P1 are errors, P2 and P3 warnings, P4 and P5 notes.

Paths are relative to the repository root and carry the uriBaseId %SRCROOT%,
which originalUriBaseIds resolves to the root on the machine that ran the
audit. partialFingerprints holds a hash of the rule and its primary file, with
no line number, so the same finding keeps its identity across commits that move
lines around.

Informational notices, such as an end-of-life Node version, travel as tool
execution notifications at level note. They are not results, because they are
not rules and do not count against the project.
"""

from __future__ import annotations

from prodpilot import __version__
from prodpilot.reporting import DOCS, FIX_HELP, LEVELS, RULES_DOCS, Item, Location, View
from prodpilot.rules import ALL_RULES, Rule

SCHEMA = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
FINGERPRINT = "prodpilotFinding/v1"
BASE = "%SRCROOT%"


def descriptor(rule: Rule) -> dict:
    """One reportingDescriptor, the way GitHub code scanning wants it described."""
    fix = rule.fix_type.value
    stack = "Node.js with Express" if rule.stack.value == "node_express" else "React with Vite"
    domain = rule.domain.value
    return {
        "id": rule.rule_id,
        "name": rule.blueprint_item_id,
        "shortDescription": {"text": rule.description},
        "fullDescription": {"text": f"{rule.description}. A {stack} rule in the {domain} "
                                    f"domain, priority {rule.priority.value}."},
        "help": {
            "text": f"Fix type {fix}: {FIX_HELP[fix]}",
            "markdown": f"**Fix type {fix}.** {FIX_HELP[fix]}",
        },
        "helpUri": f"{RULES_DOCS}#{domain.replace('_', '-')}",
        "defaultConfiguration": {"level": LEVELS[rule.priority]},
        "properties": {
            "tags": ["production-readiness", domain, rule.stack.value],
            "domain": domain,
            "priority": rule.priority.value,
            "stack": rule.stack.value,
            "fixType": fix,
            "checkType": rule.check_type.value,
            "scope": rule.scope.value,
        },
    }


def location(where: Location, ident: int | None = None) -> dict:
    physical: dict = {"artifactLocation": {"uri": where.uri, "uriBaseId": BASE}}
    if where.line:
        physical["region"] = {"startLine": where.line}
    out: dict = {"physicalLocation": physical}
    if ident is not None:
        out["id"] = ident
    return out


def result(item: Item, index: dict[str, int]) -> dict:
    out: dict = {
        "ruleId": item.rule_id,
        "ruleIndex": index[item.rule_id],
        "level": item.level,
        "message": {"text": item.message},
        "locations": [location(item.locations[0])],
        "partialFingerprints": {FINGERPRINT: item.fingerprint},
        "properties": {
            "priority": item.result.priority.value,
            "domain": item.result.domain.value,
            "fixType": item.result.fix_type.value,
            "attachedToManifest": item.attached,
        },
    }
    if len(item.locations) > 1:
        out["relatedLocations"] = [location(w, n) for n, w in
                                   enumerate(item.locations[1:], start=1)]
    return out


def to_sarif(view: View) -> dict:
    """The SARIF 2.1.0 log for one audited project."""
    index = {r.rule_id: n for n, r in enumerate(ALL_RULES)}
    report = view.report
    run = {
        "tool": {"driver": {
            "name": "ProdPilot",
            "version": __version__,
            "informationUri": DOCS,
            "rules": [descriptor(r) for r in ALL_RULES],
        }},
        "originalUriBaseIds": {BASE: {"uri": view.base.as_uri().rstrip("/") + "/"}},
        "invocations": [{
            "executionSuccessful": True,
            "toolExecutionNotifications": [
                {"level": "note", "message": {"text": text}} for text in view.notices],
        }],
        "results": [result(item, index) for item in view.items],
        "properties": {
            "project": report.project,
            "stack": report.stack.value,
            "score": report.score,
            "band": report.band.value,
        },
    }
    return {"$schema": SCHEMA, "version": "2.1.0", "runs": [run]}

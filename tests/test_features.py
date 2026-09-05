"""Feature vector tests for Phase 5 module 5.2.

Everything here runs against the sample projects the audit tests already use, so
every vector comes from a real audit of real files rather than a constructed
report.

The load bearing test is the hand verified vector. Every count in it was worked
out from the audit's own output by hand before it was written down, so the test
would catch the vector being built correctly by accident.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prodpilot import audit, features
from prodpilot.blueprint import Domain, Priority, Stack
from prodpilot.features import (
    DOMAINS,
    FEATURES,
    NEGATIVE,
    PRIORITIES,
    REPO,
    SIZE,
    FeatureError,
    Row,
    Skipped,
    build,
    matrix,
    write_names,
    of,
    vector,
    write,
)
from prodpilot.rules import rules_for_stack

SAMPLES = Path(__file__).resolve().parent / "samples"


def at(name: str) -> int:
    return FEATURES.index(name)


def row_for(sample: str) -> Row:
    return of(SAMPLES / sample, sample, REPO)


# --------------------------------------------------------------------------
# the shape, which is the contract with 5.3 and 5.4
# --------------------------------------------------------------------------


def test_there_are_exactly_twenty_five_features():
    """Section 6 states the count, so it is asserted rather than assumed."""
    assert SIZE == 25
    assert len(FEATURES) == 25


def test_the_count_falls_out_of_the_ruleset():
    """9 domains, 9 domains again, 6 priorities, 1 stack flag.

    Written as arithmetic over the real enums so that adding a domain or a
    priority tier breaks this test rather than silently changing the vector.
    """
    assert len(list(Domain)) == 9
    assert len(list(Priority)) == 6
    assert len(list(Domain)) * 2 + len(list(Priority)) + 1 == SIZE


def test_every_feature_name_is_unique():
    assert len(set(FEATURES)) == len(FEATURES)


def test_the_order_is_the_one_5_3_and_5_4_will_index_by():
    assert FEATURES[:9] == tuple(f"assessed_{d}" for d in DOMAINS)
    assert FEATURES[9:18] == tuple(f"failed_{d}" for d in DOMAINS)
    assert FEATURES[18:24] == tuple(f"failed_{p.lower()}" for p in PRIORITIES)
    assert FEATURES[24] == "is_node"


def test_the_audit_score_is_not_one_of_the_features():
    """It is carried on the row instead, for the reason in the module docstring."""
    assert not any("score" in name for name in FEATURES)


# --------------------------------------------------------------------------
# the values, which have to be integers and nothing else
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sample",
    ["node_express_insecure", "node_express_ready", "node_express_hardened",
     "react_vite_ready", "react_vite_app", "react_vite_hardened"],
)
def test_every_vector_is_twenty_five_plain_integers(sample: str):
    values = row_for(sample).values

    assert len(values) == SIZE
    for name, value in zip(FEATURES, values):
        assert type(value) is int, f"{name} is {type(value).__name__}"
        assert value >= 0


def test_no_value_is_a_bool_dressed_as_an_integer():
    """A bool passes an isinstance int check and is not what was asked for."""
    for value in row_for("react_vite_ready").values:
        assert not isinstance(value, bool)


def test_nothing_is_absent():
    """scikit-learn needs every value numeric, finite and present."""
    values = row_for("node_express_insecure").values

    assert all(v is not None for v in values)
    assert all(v == v for v in values)


def test_the_stack_flag_is_binary_and_correct():
    assert row_for("node_express_insecure").values[at("is_node")] == 1
    assert row_for("react_vite_ready").values[at("is_node")] == 0


# --------------------------------------------------------------------------
# the vector says what the audit actually found
# --------------------------------------------------------------------------


def test_a_known_project_produces_a_hand_verified_vector():
    """Every number below was read off the audit by hand before being written.

    react_vite_ready audits as 22 rules, 17 passing, 1 failing (STR-003, a P3 in
    the structure domain) and 4 skipped (ENV-003 and ENV-004 in environment,
    STR-004 in structure, GIT-007 in git hygiene, which has no repository to
    scan).
    """
    expected = (
        # assessed per domain: security, secrets, environment, build,
        # connectivity, api, structure, observability, git hygiene
        2, 2, 1, 7, 0, 0, 1, 2, 3,
        # failed per domain, only STR-003 in structure
        0, 0, 0, 0, 0, 0, 1, 0, 0,
        # failed per priority, STR-003 is a P3
        0, 0, 0, 1, 0, 0,
        # not a Node project
        0,
    )

    assert row_for("react_vite_ready").values == expected


def test_the_assessed_counts_match_the_audit():
    report = audit.run(SAMPLES / "node_express_insecure")
    values = row_for("node_express_insecure").values

    for domain in DOMAINS:
        assessed = len([r for r in report.results if r.domain.value == domain and r.scored])
        assert values[at(f"assessed_{domain}")] == assessed, domain


def test_the_failed_counts_match_the_audit():
    report = audit.run(SAMPLES / "node_express_insecure")
    values = row_for("node_express_insecure").values

    for domain in DOMAINS:
        failed = len([r for r in report.issues if r.domain.value == domain])
        assert values[at(f"failed_{domain}")] == failed, domain


def test_the_priority_counts_match_the_audit():
    report = audit.run(SAMPLES / "node_express_insecure")
    values = row_for("node_express_insecure").values

    for priority in PRIORITIES:
        failed = len([r for r in report.issues if r.priority.value == priority])
        assert values[at(f"failed_{priority.lower()}")] == failed, priority


def test_the_domain_failures_sum_to_the_priority_failures():
    """Two views of the same failures, so they have to agree."""
    values = row_for("node_express_insecure").values

    by_domain = sum(values[at(f"failed_{d}")] for d in DOMAINS)
    by_priority = sum(values[at(f"failed_{p.lower()}")] for p in PRIORITIES)

    assert by_domain == by_priority
    assert by_domain == len(audit.run(SAMPLES / "node_express_insecure").issues)


def test_the_score_is_carried_but_kept_off_the_vector():
    row = row_for("react_vite_ready")

    assert row.score == audit.run(SAMPLES / "react_vite_ready").score
    assert "score" in row.to_dict()
    assert len(row.values) == SIZE
    assert not any("score" in name for name in FEATURES)


# --------------------------------------------------------------------------
# a domain that was not checked is not a domain that passed
# --------------------------------------------------------------------------


def test_a_domain_the_stack_has_no_rules_for_reads_as_nothing_assessed():
    """React declares connectivity and api not applicable, per module 1.2."""
    values = row_for("react_vite_ready").values

    for domain in ("connectivity", "api"):
        assert not [r for r in rules_for_stack(Stack.REACT_VITE) if r.domain.value == domain]
        assert values[at(f"assessed_{domain}")] == 0
        assert values[at(f"failed_{domain}")] == 0


def test_the_same_two_domains_are_assessed_for_node():
    """The proof that zero means absent rather than always empty.

    node_express_hardened opens a database connection, so the connectivity
    rules have something to judge and are actually assessed.
    """
    values = row_for("node_express_hardened").values

    assert values[at("assessed_connectivity")] > 0
    assert values[at("assessed_api")] > 0


def test_a_stack_with_no_rules_and_a_stack_that_skipped_them_both_read_zero():
    """A deliberate limit of the design, recorded rather than hidden.

    Nothing assessed means the audit could not judge the domain, and the vector
    does not say why. React has no connectivity rules at all; this Node project
    has two and skipped both because it opens no database connection. The stack
    flag is what lets a model tell the two apart, since connectivity is always
    zero for React and varies for Node.
    """
    react = row_for("react_vite_ready").values
    node = row_for("node_express_insecure").values

    assert react[at("assessed_connectivity")] == node[at("assessed_connectivity")] == 0
    assert react[at("is_node")] != node[at("is_node")]


def test_checked_and_clean_is_a_different_row_than_never_checked():
    """The distinction module 1.2 required, visible in the numbers."""
    values = row_for("react_vite_ready").values

    clean = (values[at("assessed_security")], values[at("failed_security")])
    absent = (values[at("assessed_connectivity")], values[at("failed_connectivity")])

    assert clean == (2, 0)
    assert absent == (0, 0)
    assert clean != absent


def test_a_rule_skipped_at_run_time_lowers_the_assessed_count():
    """Git hygiene has 4 React rules, and the history rule cannot run here."""
    values = row_for("react_vite_ready").values
    declared = len([r for r in rules_for_stack(Stack.REACT_VITE)
                    if r.domain.value == "git_hygiene"])

    assert declared == 4
    assert values[at("assessed_git_hygiene")] == 3


# --------------------------------------------------------------------------
# a project that cannot be audited is recorded, not dropped
# --------------------------------------------------------------------------


def test_a_missing_project_is_refused_with_a_reason():
    with pytest.raises(FeatureError) as caught:
        of(SAMPLES / "no-such-project", "nobody/nothing", REPO)

    assert "not a project directory" in str(caught.value)


def test_a_project_of_no_supported_stack_is_refused():
    with pytest.raises(FeatureError) as caught:
        of(SAMPLES / "unrecognized_python_service", "x/y", REPO)

    assert str(caught.value)


def test_a_report_that_assessed_nothing_has_no_vector():
    """A row of zeros would look exactly like a clean project."""
    report = audit.run(SAMPLES / "unrecognized_python_service")
    assert report.results == ()

    with pytest.raises(FeatureError):
        vector(report)


def test_a_project_the_audit_engine_cannot_process_is_recorded(monkeypatch):
    """One pathological repository must cost itself, not the whole run.

    A real repository in the collected corpus nests a syntax tree deeply enough
    that reading the parser output exhausts Python's recursion limit. That
    escaped as an unhandled error and ended a 686 item build, so it is caught
    and named here.
    """
    def boom(root):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(features.audit, "run", boom)

    with pytest.raises(FeatureError) as caught:
        of(SAMPLES / "react_vite_ready", "a/b", REPO)

    assert "RecursionError" in str(caught.value)


def test_the_reason_names_the_failure_type(monkeypatch):
    """So an exclusion is never unexplained in the accounting."""
    monkeypatch.setattr(features.audit, "run",
                        lambda root: (_ for _ in ()).throw(ValueError("odd rule id")))

    with pytest.raises(FeatureError) as caught:
        of(SAMPLES / "react_vite_ready", "a/b", REPO)

    assert "ValueError" in str(caught.value)
    assert "odd rule id" in str(caught.value)


def test_a_broken_manifest_is_refused(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "package.json").write_text("{ not json", encoding="utf-8")

    with pytest.raises(FeatureError):
        of(root, "x/y", REPO)


# --------------------------------------------------------------------------
# walking the dataset
# --------------------------------------------------------------------------


class Entry:
    """Stands in for a manifest entry, which is all build needs of one."""

    def __init__(self, name: str, sample: str) -> None:
        self.name = name
        self.sample = sample
        self.commit = "c" * 40


def test_every_item_yields_exactly_one_result():
    """So the counts in a report always add up to what went in."""
    entries = [Entry("a/one", "node_express_insecure"), Entry("b/two", "nowhere")]
    negatives = [
        {"name": "n1", "path": str(SAMPLES / "react_vite_ready"), "rule_id": "BLD-008"},
        {"name": "n2", "path": str(SAMPLES / "no-such-project"), "rule_id": "BLD-009"},
    ]

    def fetch(entry):
        if entry.sample == "nowhere":
            raise RuntimeError("the commit no longer resolves")
        return SAMPLES / entry.sample

    out = list(build(entries, negatives, fetch))

    assert len(out) == 4
    assert [type(o).__name__ for o in out] == ["Row", "Skipped", "Row", "Skipped"]


def test_a_repository_that_cannot_be_fetched_is_recorded():
    def fetch(entry):
        raise RuntimeError("the commit no longer resolves")

    out = list(build([Entry("a/one", "x")], [], fetch))

    assert isinstance(out[0], Skipped)
    assert out[0].kind == REPO
    assert "no longer resolves" in out[0].reason


def test_a_negative_carries_the_rule_it_violates():
    negatives = [{"name": "n1", "path": str(SAMPLES / "react_vite_app"),
                  "rule_id": "BLD-008"}]

    out = list(build([], negatives, lambda e: Path()))

    assert out[0].kind == NEGATIVE
    assert out[0].rule_id == "BLD-008"


def test_no_label_is_attached_anywhere():
    """Labelling is module 5.3. A row must not carry an outcome."""
    row = row_for("react_vite_ready")

    payload = row.to_dict()
    assert set(payload) == {
        "name", "kind", "stack", "commit", "rule_id", "score", "values",
    }
    for word in ("label", "outcome", "deployed", "target", "y"):
        assert word not in payload


# --------------------------------------------------------------------------
# what gets written, and what a model will be handed
# --------------------------------------------------------------------------


def test_the_matrix_is_the_shape_scikit_learn_expects():
    """A list of equal length lists of numbers is array-like for numpy.asarray."""
    rows = [row_for("node_express_insecure"), row_for("react_vite_ready")]

    made = matrix(rows)

    assert len(made) == 2
    assert {len(r) for r in made} == {SIZE}
    assert all(type(v) is int for r in made for v in r)


def test_the_matrix_keeps_the_row_order():
    rows = [row_for("react_vite_ready"), row_for("node_express_insecure")]

    made = matrix(rows)

    assert made[0][at("is_node")] == 0
    assert made[1][at("is_node")] == 1


def test_the_written_rows_stay_traceable(tmp_path: Path):
    rows = [row_for("node_express_insecure")]
    path = tmp_path / "features.jsonl"

    write(rows, path)

    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["name"] == "node_express_insecure"
    assert len(lines[0]["values"]) == SIZE


def test_the_feature_names_are_written_beside_the_matrix(tmp_path: Path):
    path = tmp_path / "features.names.json"

    write_names(path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["size"] == SIZE
    assert payload["features"] == list(FEATURES)

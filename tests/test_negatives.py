"""Synthetic negative tests for Phase 5 module 5.1.

Everything here runs offline, against the sample projects the audit tests
already use. Nothing is mocked: the breaks are applied to real copies and the
verdict comes from the real checkers, because a negative whose label was
assumed rather than measured is worse than no negative at all.

The load-bearing test is the one that refuses a break which moves a second rule.
Section 6 asks for one violation at a time, and an edit that quietly breaks a
neighbour would put a wrong label into the training set.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prodpilot import negatives
from prodpilot.findings import Status
from prodpilot.negatives import (
    BREAKS,
    Break,
    BreakError,
    Negative,
    add,
    build,
    drop,
    drop_key,
    drop_line,
    make,
    sub,
    write,
)
from prodpilot.rules import ALL_RULES, get_rule
from prodpilot.verify import check

SAMPLES = Path(__file__).resolve().parent / "samples"
SOURCES = {
    "node_express": SAMPLES / "node_express_ready",
    "react_vite": SAMPLES / "react_vite_ready",
}

# Enough breaks to cover every edit helper and both stacks, without running a
# full audit for all 28 in the suite. The complete set is exercised by the
# collection script, and its result is reported.
SOME = ("BLD-002", "BLD-004", "SEC-001", "ENV-002", "SCR-002", "BLD-011", "SEC-006")


def one(rule_id: str) -> Break:
    return next(b for b in BREAKS if b.rule_id == rule_id)


def source_for(rule_id: str) -> Path:
    return SOURCES[get_rule(rule_id).stack.value]


# --------------------------------------------------------------------------
# every break names a real rule from the frozen store
# --------------------------------------------------------------------------


def test_every_break_targets_a_rule_that_exists():
    """The store is the source of truth for what a violation is."""
    for brk in BREAKS:
        assert get_rule(brk.rule_id) is not None, brk.rule_id


def test_a_break_against_an_unknown_rule_is_refused():
    with pytest.raises(BreakError):
        Break("NOPE-000", "invent a defect", drop("anything"))


def test_no_rule_is_broken_twice():
    ids = [b.rule_id for b in BREAKS]

    assert len(ids) == len(set(ids))


def test_every_break_carries_a_note_a_person_can_read():
    for brk in BREAKS:
        assert len(brk.note.split()) >= 3, brk.rule_id


def test_the_breaks_cover_both_stacks():
    stacks = {get_rule(b.rule_id).stack.value for b in BREAKS}

    assert stacks == {"node_express", "react_vite"}


def test_every_broken_rule_has_a_fix_path_in_phase_three():
    """A negative is the inverse of a fix, so the rule must be fixable."""
    from prodpilot import constraints, extraction, templates

    for brk in BREAKS:
        owns = (templates.covers(brk.rule_id) or extraction.covers(brk.rule_id)
                or constraints.covers(brk.rule_id))
        assert owns, f"{brk.rule_id} has no fix path"


# --------------------------------------------------------------------------
# the edits
# --------------------------------------------------------------------------


def test_dropping_a_file_reports_whether_it_was_there(tmp_path: Path):
    (tmp_path / "Dockerfile").write_text("FROM node:20\n", encoding="utf-8")

    assert drop("Dockerfile")(tmp_path) is True
    assert drop("Dockerfile")(tmp_path) is False


def test_dropping_a_directory_removes_it(tmp_path: Path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")

    assert drop(".github")(tmp_path) is True
    assert not (tmp_path / ".github").exists()


def test_dropping_a_line_leaves_the_rest(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("node_modules\n.env\ndist\n", encoding="utf-8")

    assert drop_line(".gitignore", ".env")(tmp_path) is True
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").split() == ["node_modules", "dist"]


def test_dropping_a_line_that_is_not_there_changes_nothing(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("node_modules\n", encoding="utf-8")

    assert drop_line(".gitignore", ".env")(tmp_path) is False
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "node_modules\n"


def test_dropping_a_json_key_keeps_the_document_valid(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "a", "scripts": {"start": "node .", "dev": "x"}}), encoding="utf-8")

    assert drop_key("package.json", "scripts", "start")(tmp_path) is True

    document = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert document["scripts"] == {"dev": "x"}
    assert document["name"] == "a"


def test_dropping_a_json_key_that_is_absent_changes_nothing(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "a"}), encoding="utf-8")

    assert drop_key("package.json", "engines")(tmp_path) is False


def test_dropping_a_key_from_unparsable_json_changes_nothing(tmp_path: Path):
    (tmp_path / "package.json").write_text("{ not json", encoding="utf-8")

    assert drop_key("package.json", "engines")(tmp_path) is False


def test_substituting_text_that_is_absent_changes_nothing(tmp_path: Path):
    (tmp_path / "a.js").write_text("const x = 1;\n", encoding="utf-8")

    assert sub("a.js", "const y", "const z")(tmp_path) is False


def test_an_edit_on_a_missing_file_changes_nothing(tmp_path: Path):
    assert drop_line("nowhere.txt", "x")(tmp_path) is False
    assert sub("nowhere.txt", "a", "b")(tmp_path) is False
    assert drop_key("nowhere.json", "a")(tmp_path) is False


def test_adding_a_file_creates_the_directories_it_needs(tmp_path: Path):
    assert add("src/keys.js", negatives.LEAK)(tmp_path) is True
    assert (tmp_path / "src" / "keys.js").read_text(encoding="utf-8") == negatives.LEAK


# --------------------------------------------------------------------------
# a negative is only recorded when the label is true
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", SOME)
def test_a_break_makes_the_rule_fail(rule_id: str, tmp_path: Path):
    """The rule passes on the source and fails on the negative, measured."""
    source = source_for(rule_id)
    assert check(source, rule_id).status is Status.PASS

    negative = make(source, tmp_path / "broken", one(rule_id))

    assert negative is not None, f"{rule_id} did not produce a negative"
    assert negative.rule_id == rule_id
    assert check(tmp_path / "broken", rule_id).status is Status.FAIL


@pytest.mark.parametrize("rule_id", SOME)
def test_a_negative_records_where_it_came_from(rule_id: str, tmp_path: Path):
    negative = make(source_for(rule_id), tmp_path / "broken", one(rule_id))

    assert negative.source == source_for(rule_id).name
    assert negative.stack == get_rule(rule_id).stack.value
    assert Path(negative.path).is_dir()
    assert negative.note


def test_a_rule_that_does_not_pass_first_cannot_be_broken(tmp_path: Path):
    """Breaking something already broken would label an unchanged project."""
    source = SAMPLES / "node_express_insecure"
    assert check(source, "SEC-002").status is Status.FAIL

    assert make(source, tmp_path / "broken", one("SEC-002")) is None


def test_an_edit_that_finds_nothing_produces_no_negative(tmp_path: Path):
    empty = Break("BLD-002", "delete a file that is not there", drop("nowhere.txt"))

    assert make(SOURCES["node_express"], tmp_path / "broken", empty) is None
    assert not (tmp_path / "broken").exists()


def test_an_edit_that_does_not_break_the_rule_produces_no_negative(tmp_path: Path):
    """A break that looks applied and changes nothing would mislabel silently."""
    harmless = Break("BLD-002", "add an unrelated file", add("NOTES.md", "hello\n"))

    assert make(SOURCES["node_express"], tmp_path / "broken", harmless) is None


def test_a_break_that_also_breaks_another_rule_is_refused(tmp_path: Path):
    """Section 6 asks for one violation at a time, so this has to be rejected.

    Deleting the Dockerfile fails BLD-001, and fails the multi stage and
    non-root rules with it, because both inspect the file that is now gone.
    """
    source = SOURCES["node_express"]
    assert check(source, "BLD-001").status is Status.PASS

    assert make(source, tmp_path / "broken", one("BLD-001")) is None
    assert not (tmp_path / "broken").exists()


def test_a_rule_that_merely_stops_being_checkable_is_not_a_second_violation(tmp_path: Path):
    """Hardcoding the port leaves ENV-001 with nothing to assess, which is
    not the same as breaking it, so the negative is kept and the move recorded."""
    negative = make(SOURCES["node_express"], tmp_path / "broken", one("ENV-002"))

    assert negative is not None
    assert any(entry.startswith("ENV-001") for entry in negative.also)


def test_a_rejected_break_leaves_no_directory_behind(tmp_path: Path):
    make(SOURCES["node_express"], tmp_path / "broken", one("BLD-001"))

    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------
# building and recording a set
# --------------------------------------------------------------------------


def test_a_build_produces_verified_negatives_for_both_stacks(tmp_path: Path):
    chosen = tuple(one(r) for r in SOME)

    made = build(SOURCES, tmp_path / "negatives", breaks=chosen)

    assert len(made) == len(chosen)
    assert {n.stack for n in made} == {"node_express", "react_vite"}
    for negative in made:
        assert check(Path(negative.path), negative.rule_id).status is Status.FAIL


def test_a_build_skips_a_break_with_no_source_for_its_stack(tmp_path: Path):
    only_node = {"node_express": SOURCES["node_express"]}

    made = build(only_node, tmp_path / "negatives", breaks=(one("SEC-006"),))

    assert made == []


def test_the_record_names_the_rule_each_negative_violates(tmp_path: Path):
    made = build(SOURCES, tmp_path / "negatives", breaks=(one("BLD-002"), one("BLD-011")))
    path = tmp_path / "negatives.jsonl"

    write(made, path)

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["rule_id"] for r in rows] == ["BLD-002", "BLD-011"]
    for row in rows:
        assert set(row) == {"name", "source", "rule_id", "note", "stack", "path", "also"}
        assert get_rule(row["rule_id"]) is not None


def test_the_record_is_ground_truth_a_later_module_can_read(tmp_path: Path):
    """5.3 labels from this, so every row must name a rule that exists."""
    made = build(SOURCES, tmp_path / "negatives", breaks=(one("SCR-002"),))
    write(made, tmp_path / "negatives.jsonl")

    rows = [json.loads(line) for line in
            (tmp_path / "negatives.jsonl").read_text(encoding="utf-8").splitlines()]
    known = {r.rule_id for r in ALL_RULES}
    assert all(row["rule_id"] in known for row in rows)


def test_a_negative_serialises_whole():
    payload = Negative("n", "s", "BLD-002", "note", "node_express", "/tmp/n").to_dict()

    assert payload["also"] == []
    json.dumps(payload)

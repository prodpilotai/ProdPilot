"""Tests for the build check behind Phase 5's build success feature.

No test here runs Docker or reaches a network. The runner is the seam: every
test hands check a scripted runner that answers the way Docker would, and
asserts on the command check sent and on what it made of the answer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prodpilot import builds, labels
from prodpilot.builds import BUILT, FAILED, UNDETERMINED, Build, BuildError

VERSIONS = {"node:lts": "v24.21.0", "node:latest": "v26.8.2", "node:22-slim": "v22.23.2",
            "node:20": "v20.20.2", "node:16": "v16.20.2", "node:12": "v12.22.12",
            "node:10": "v10.24.1"}


@pytest.fixture(autouse=True)
def fresh():
    builds._images = None
    yield
    builds._images = None


def runner(*answers, versions=VERSIONS):
    """A scripted Docker: image version probes, then one answer per build."""
    sent: list[list[str]] = []
    queue = list(answers)

    def run(args, limit):
        sent.append(args)
        if "--network" in args and args[-2:] == ["node", "--version"]:
            image = args[-3]
            return (0, versions[image] + "\n") if image in versions else (125, "docker: no such image")
        return queue.pop(0)

    return run, sent


def project(tmp_path: Path, **files: str) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    (root / "package.json").write_text(files.pop("package", "{}"), encoding="utf-8")
    for name, text in files.items():
        (root / name.replace("_", ".", 1) if name.startswith("_") else root / name).write_text(
            text, encoding="utf-8")
    return root


def builds_of(sent):
    return [a for a in sent if "--name" in a]


# --------------------------------------------------------------------------
# which Node major
# --------------------------------------------------------------------------

KNOWN = [10, 12, 16, 20, 22, 24, 26]


@pytest.mark.parametrize("ask, expected", [
    ("", 24), ("22.16.0", 22), ("v12.13.1", 12), ("22.x", 22), ("^16.0.0", 16),
    ("20", 20), (">=20 <21", 20), (">=18", 26), (">=0.10.0", 26), ("*", 26),
    ("lts/*", 24), ("lts/iron", 20), ("lts/hydrogen", 18), ("node", 26),
])
def test_a_requested_version_resolves_the_way_render_resolves_it(ask, expected):
    assert builds.major(ask, KNOWN) == expected


def test_the_version_is_read_in_render_s_order(tmp_path: Path):
    root = project(tmp_path, package=json.dumps({"engines": {"node": "16.x"}}))
    assert builds.requested(root) == ("16.x", "engines")

    (root / ".nvmrc").write_text("20\n", encoding="utf-8")
    assert builds.requested(root) == ("20", ".nvmrc")

    (root / ".node-version").write_text("22.1.0\n", encoding="utf-8")
    assert builds.requested(root) == ("22.1.0", ".node-version")


def test_a_project_asking_for_nothing_gets_render_s_default(tmp_path: Path):
    assert builds.requested(project(tmp_path)) == ("", "default")


def test_images_are_mapped_by_the_version_they_actually_carry():
    run, _ = runner()

    assert builds.images(run) == {24: "node:lts", 26: "node:latest", 22: "node:22-slim",
                                  20: "node:20", 16: "node:16", 12: "node:12", 10: "node:10"}


# --------------------------------------------------------------------------
# what is run
# --------------------------------------------------------------------------


@pytest.mark.parametrize("stack", ["react_vite", "node_express"])
def test_the_command_is_the_one_module_5_3_gave_render(tmp_path: Path, stack):
    run, sent = runner((0, "ok"))

    builds.check(project(tmp_path), stack, run=run)

    script = builds_of(sent)[0][-1]
    assert script.endswith(labels.serves(stack)[0])


def test_the_project_is_mounted_read_only_and_copied_before_it_is_built(tmp_path: Path):
    root = project(tmp_path)
    run, sent = runner((0, "ok"))

    builds.check(root, "react_vite", run=run)

    args = builds_of(sent)[0]
    assert f"{root.resolve()}:/src:ro" in args
    assert args[-1].startswith("cp -a /src/. /app && cd /app && ")


def test_the_requested_major_picks_its_image(tmp_path: Path):
    root = project(tmp_path, package=json.dumps({"engines": {"node": ">=20 <21"}}))
    run, sent = runner((0, "ok"))

    found = builds.check(root, "node_express", run=run)

    assert found.image == "node:20" and found.wanted == 20 and not found.fallback


def test_a_major_no_local_image_carries_is_built_on_the_default_and_marked(tmp_path: Path):
    root = project(tmp_path, package=json.dumps({"engines": {"node": "18.17.1"}}))
    run, _ = runner((0, "ok"))

    found = builds.check(root, "node_express", run=run)

    assert found.wanted == 18
    assert found.image == "node:lts"
    assert found.fallback is True


# --------------------------------------------------------------------------
# what it makes of the answer
# --------------------------------------------------------------------------


def test_a_build_that_succeeds_is_a_one(tmp_path: Path):
    run, _ = runner((0, "vite v5 built in 2s"))

    found = builds.check(project(tmp_path), "react_vite", run=run)

    assert found.outcome == BUILT and found.built == 1 and found.attempts == 1


def test_a_build_that_fails_is_a_zero_with_its_reason(tmp_path: Path):
    run, _ = runner((1, "src/main.jsx\nerror during build:\nCould not resolve ./App"))

    found = builds.check(project(tmp_path), "react_vite", run=run)

    assert found.outcome == FAILED and found.built == 0
    assert "error during build" in found.reason


def test_a_network_failure_is_retried_once(tmp_path: Path):
    run, sent = runner((1, "npm ERR! code ECONNRESET"), (0, "added 120 packages"))

    found = builds.check(project(tmp_path), "node_express", run=run)

    assert found.outcome == BUILT and found.attempts == 2
    assert len(builds_of(sent)) == 2


def test_two_network_failures_are_undetermined_never_a_zero(tmp_path: Path):
    run, _ = runner((1, "npm ERR! code ETIMEDOUT"), (1, "npm ERR! code EAI_AGAIN"))

    found = builds.check(project(tmp_path), "node_express", run=run)

    assert found.outcome == UNDETERMINED and found.built is None


def test_a_build_that_runs_out_of_time_is_undetermined(tmp_path: Path):
    run, _ = runner((None, "still installing"))

    found = builds.check(project(tmp_path), "react_vite", run=run)

    assert found.outcome == UNDETERMINED and found.built is None
    assert "did not finish" in found.reason


def test_docker_refusing_the_container_is_an_error_not_a_result(tmp_path: Path):
    """The fault that recorded four fake failures in the first timing run."""
    run, _ = runner((125, 'docker: Error response from daemon: "data\\\\negatives" includes '
                          'invalid characters for a local volume name'))

    with pytest.raises(BuildError):
        builds.check(project(tmp_path), "react_vite", run=run)


def test_a_project_that_is_not_there_is_an_error(tmp_path: Path):
    with pytest.raises(BuildError):
        builds.check(tmp_path / "missing", "react_vite", run=runner()[0])


# --------------------------------------------------------------------------
# reading recorded results
# --------------------------------------------------------------------------


def test_only_real_results_are_read_back(tmp_path: Path):
    path = tmp_path / "builds.jsonl"
    records = [
        {"name": "a/b", "kind": "repo", "rule_id": "", "outcome": BUILT, "exit": 0},
        {"name": "c/d", "kind": "repo", "rule_id": "", "outcome": FAILED, "exit": 1},
        {"name": "e/f", "kind": "repo", "rule_id": "", "outcome": UNDETERMINED, "exit": None},
        {"name": "g_x", "kind": "negative", "rule_id": "GIT-005", "outcome": FAILED, "exit": 125,
         "tail": "docker: Error response from daemon: invalid volume name"},
        {"name": "h/i", "kind": "repo", "rule_id": "", "outcome": FAILED, "exit": 127,
         "tail": "> vite build\nsh: 1: pnpm: not found\n"},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")

    found = builds.read(path)

    assert set(found) == {("a/b", "repo", ""), ("c/d", "repo", ""), ("h/i", "repo", "")}


def test_a_command_missing_inside_the_build_is_a_failure_not_a_refusal(tmp_path: Path):
    """Exit 127 is also what a shell returns for a missing command, which the
    first rebuild of the matrix wrongly discarded as a harness fault."""
    run, _ = runner((127, "> tsc && vite build\nsh: 1: pnpm: not found\n"))

    found = builds.check(project(tmp_path), "react_vite", run=run)

    assert found.outcome == FAILED and found.built == 0
    assert "pnpm: not found" in found.reason


def test_a_result_serialises_with_its_value():
    assert Build(BUILT).to_dict()["built"] == 1
    assert Build(FAILED).to_dict()["built"] == 0
    assert Build(UNDETERMINED).to_dict()["built"] is None


def test_the_reason_names_the_error_not_where_npm_put_its_log():
    """The first real failure in the dataset pass, as npm printed it."""
    log = "\n".join([
        "npm error code ERESOLVE",
        "npm error ERESOLVE unable to resolve dependency tree",
        "npm error Could not resolve dependency:",
        'npm error peer less@"^2.7.3" from @zeit/next-less@1.0.1',
        "npm error",
        "npm error For a full report see:",
        "npm error /root/.npm/_logs/2026-09-11T23_08_00_809Z-eresolve-report.txt",
        "npm error A complete log of this run can be found in: /root/.npm/_logs/x-debug-0.log",
    ])

    found = builds.cause(log)

    assert "ERESOLVE" in found
    assert "_logs" not in found and "full report" not in found

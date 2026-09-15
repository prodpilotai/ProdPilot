"""What ProdPilot needs on the machine besides itself."""

from __future__ import annotations

import subprocess

from prodpilot import buildtest, prereqs


def answers(stdout: str = "", code: int = 0):
    def run(args, **_):
        return subprocess.CompletedProcess(args, code, stdout, "")
    return run


def found(name: str):
    return lambda tool: f"/usr/bin/{tool}" if tool == name else None


def test_git_is_found_with_its_version():
    need = prereqs.git(which=found("git"), run=answers("git version 2.47.0\n"))

    assert need.ok is True
    assert need.detail == "git version 2.47.0"


def test_a_missing_git_says_what_it_is_for():
    need = prereqs.git(which=lambda tool: None, run=answers())

    assert need.ok is False
    assert "install Git" in need.detail


def test_node_that_fails_to_run_is_not_counted():
    need = prereqs.node(which=found("node"), run=answers("", code=1))

    assert need.ok is False
    assert "Node.js 20" in need.detail


def test_node_is_found_with_its_version():
    need = prereqs.node(which=found("node"), run=answers("v20.18.0\n"))

    assert need.ok is True
    assert need.detail == "v20.18.0"


def test_an_unreachable_docker_daemon_says_to_start_it():
    def refuse():
        raise buildtest.BuildUnavailable("cannot reach the Docker daemon: refused")

    need = prereqs.docker(connect=refuse)

    assert need.ok is False
    assert "start Docker" in need.detail
    assert "refused" in need.detail


def test_a_reachable_docker_daemon_reports_its_version():
    class Daemon:
        def version(self):
            return {"Version": "29.7.2"}

    need = prereqs.docker(connect=Daemon)

    assert need.ok is True
    assert need.detail == "daemon 29.7.2"


def test_git_is_found_on_this_machine():
    """The suite itself runs git, so it is always there when these tests run."""
    assert prereqs.git().ok is True


def test_everything_is_checked_in_the_order_a_run_uses_it():
    assert [need.name for need in prereqs.check()] == ["git", "Node.js", "Docker"]


def test_doctor_reports_each_tool_and_fails_when_one_is_missing(monkeypatch):
    from typer.testing import CliRunner

    from prodpilot.cli import app

    monkeypatch.setattr(prereqs, "check", lambda: [
        prereqs.Need("git", True, "git version 2.47.0"),
        prereqs.Need("Docker", False, "cannot reach the Docker daemon; start Docker"),
    ])

    result = CliRunner().invoke(app, ["doctor"])

    assert "git: git version 2.47.0" in result.output
    assert "start Docker" in result.output
    assert "Docker missing" in result.output
    assert result.exit_code == 1

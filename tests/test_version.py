"""The version has one source, and everything that names it follows."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from prodpilot import __version__

REPO = Path(__file__).resolve().parents[1]


def test_the_version_is_a_pep_440_release_or_candidate() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+(rc\d+)?", __version__), __version__


def test_pyproject_reads_the_version_from_the_package() -> None:
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in project["project"]
    assert project["project"]["dynamic"] == ["version"]
    assert project["tool"]["hatch"]["version"]["path"] == "src/prodpilot/__init__.py"


def test_the_action_installs_this_version_by_default() -> None:
    text = (REPO / "action.yml").read_text(encoding="utf-8")
    block = re.search(r"^  version:\n((?:    .*\n)+)", text, re.M)
    assert block, "action.yml has no version input"
    assert f'default: "{__version__}"' in block.group(1)


def test_the_release_workflow_checks_the_tag_against_the_package() -> None:
    text = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "src/prodpilot/__init__.py" in text
    assert "pyproject.toml" not in text.split("Refuse a tag", 1)[1].split("- name:", 1)[0]


def test_the_changelog_has_a_section_for_this_release() -> None:
    base = re.sub(r"rc\d+$", "", __version__)
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## {re.escape(base)}, ", text, re.M)

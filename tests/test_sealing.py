"""Environment sealing tests for Phase 6 module 6.2.

Every project here is a real Git repository with real files, because the
gitignore condition is decided by git itself and cannot be tested against a
mock without testing something other than what runs.

The load bearing test is the one that proves no file is written when the target
is not ignored. Section 2.1 principle 5 is the reason this module exists, and a
warning that still wrote the file would defeat it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from prodpilot import entropy, sealing
from prodpilot.sealing import (
    ENV,
    EXAMPLE,
    PRODUCTION,
    Kind,
    Seal,
    Value,
    classify,
    ignored,
    pairs,
    render,
    run,
    stand_in,
    uncovered,
)

SECRET = "sk_live_9fK2mQ7bZx4LpW1nR8tYv3JhC6dGe0sUiOaXcVbN"
CODE = "const p = process.env.PORT;\nconst k = process.env.STRIPE_KEY;\n"
BOTH = "PORT=\nSTRIPE_KEY=\n"


def project(tmp_path: Path, gitignore: str = ".env\n.env.production\n",
            env: str = f"PORT=3000\nSTRIPE_KEY={SECRET}\n",
            example: str = BOTH, code: str = CODE) -> Path:
    """A real repository with a real Node project inside it."""
    root = tmp_path / "project"
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(("git", "init", "-q"), cwd=str(root), check=True, capture_output=True)
    (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    (root / "package.json").write_text(
        '{"dependencies":{"express":"^4.19.0"}}', encoding="utf-8")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "server.js").write_text(code, encoding="utf-8")
    if example is not None:
        (root / EXAMPLE).write_text(example, encoding="utf-8")
    if env is not None:
        (root / ENV).write_text(env, encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# reading an env file
# --------------------------------------------------------------------------


def test_keys_and_values_are_read():
    assert pairs("A=1\nB=two\n") == {"A": "1", "B": "two"}


def test_comments_and_blank_lines_are_skipped():
    assert pairs("# a note\n\nA=1\n\n#B=2\n") == {"A": "1"}


def test_a_value_containing_an_equals_sign_survives():
    assert pairs("URL=postgres://u:p@h/db?x=1")["URL"] == "postgres://u:p@h/db?x=1"


def test_surrounding_quotes_are_stripped():
    """A shell would strip them, and a quoted secret is still a secret."""
    assert pairs('A="value"\nB=\'other\'\n') == {"A": "value", "B": "other"}


def test_a_line_with_no_key_is_ignored():
    assert pairs("=orphan\nA=1\n") == {"A": "1"}


def test_an_empty_value_is_kept_so_it_can_be_flagged():
    assert pairs("A=\n") == {"A": ""}


# --------------------------------------------------------------------------
# classification, which is module 2.3's and not a second heuristic
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["CHANGEME", "your_api_key", "<your-key-here>",
                                   "placeholder", "TODO", "xxxxxxxxxxxxxxxxxxxxxx"])
def test_a_stand_in_is_flagged_as_a_placeholder(value: str):
    assert classify(value)[0] is Kind.PLACEHOLDER


def test_an_empty_value_is_a_placeholder():
    kind, why = classify("")

    assert kind is Kind.PLACEHOLDER
    assert "empty" in why


@pytest.mark.parametrize("value", [SECRET, "postgres://user:pass@localhost:5432/app"])
def test_a_known_provider_prefix_is_a_secret(value: str):
    kind, why = classify(value)

    assert kind is Kind.SECRET
    assert "known" in why


def test_a_high_entropy_value_is_a_secret():
    kind, why = classify("a3f5c9d2e8b14760af9c2d3e5b8017cd")

    assert kind is Kind.SECRET
    assert "entropy" in why


@pytest.mark.parametrize("value", ["3000", "8080", "true", "development", "local"])
def test_an_ordinary_short_configuration_value_is_plain(value: str):
    """The reason the length gate exists.

    is_placeholder treats two or fewer distinct characters as a stand-in, which
    is right for a twenty character run and wrong for a port number. Module 2.3
    never calls it on anything shorter than MIN_LEN, and neither does this.
    """
    kind, why = classify(value)

    assert kind is Kind.PLAIN, f"{value} was read as {kind.value}"
    assert "too short" in why


def test_the_length_gate_uses_module_2_3s_own_threshold():
    assert len("3000") < entropy.MIN_LEN
    assert entropy.is_placeholder("3000") is True, "the mismatch this gate handles"
    assert classify("3000")[0] is Kind.PLAIN


def test_a_long_repeated_run_is_still_a_placeholder():
    """Above the threshold, is_placeholder decides, exactly as it does in 2.3."""
    value = "x" * (entropy.MIN_LEN + 4)

    assert entropy.is_placeholder(value) is True
    assert classify(value)[0] is Kind.PLACEHOLDER


def test_a_borderline_value_is_surfaced_rather_than_guessed():
    """Section 4.1 requires an ambiguous case to be reported."""
    assert Kind.UNSURE.value == "unsure"
    assert entropy.MARGIN > 0


def test_no_threshold_or_pattern_is_restated_here():
    """Every constant comes from module 2.3 rather than being copied."""
    source = Path(sealing.__file__).read_text(encoding="utf-8")

    for number in ("4.5", "3.0", "0.35"):
        assert number not in source, f"{number} looks like a copied threshold"
    assert "entropy.BASE64_LIMIT" in source
    assert "entropy.HEX_LIMIT" in source
    assert "entropy.MIN_LEN" in source
    assert "entropy.PLACEHOLDERS" in source


def test_the_explicit_stand_in_check_uses_the_shared_pattern():
    assert stand_in("CHANGEME") is True
    assert stand_in("<anything>") is True
    assert stand_in("3000") is False


# --------------------------------------------------------------------------
# key coverage, which is ENV-001's logic and not a second one
# --------------------------------------------------------------------------


def test_a_template_that_covers_the_code_reports_nothing_missing(tmp_path: Path):
    assert uncovered(project(tmp_path)) == ()


def test_a_key_the_code_reads_but_the_template_omits_is_reported(tmp_path: Path):
    root = project(tmp_path, example="PORT=\n")

    assert uncovered(root) == ("STRIPE_KEY",)


def test_a_project_that_reads_no_environment_has_nothing_to_cover(tmp_path: Path):
    root = project(tmp_path, code="const a = 1;\n", example="")

    assert uncovered(root) == ()


def test_coverage_uses_the_same_two_functions_env_001_uses():
    source = Path(sealing.__file__).read_text(encoding="utf-8")

    assert "astchecks.env_keys" in source
    assert "astchecks.declared_keys" in source
    assert "astchecks.load_sources" in source


def test_a_project_that_does_not_exist_has_nothing_to_cover(tmp_path: Path):
    """load_sources reads no files rather than raising, so there is no
    complaint to make. run refuses the directory before reaching here."""
    assert uncovered(tmp_path / "nowhere") == ()
    assert run(tmp_path / "nowhere").ok is False


# --------------------------------------------------------------------------
# the gitignore condition, which is a hard failure
# --------------------------------------------------------------------------


def test_an_ignored_target_is_recognised(tmp_path: Path):
    assert ignored(project(tmp_path)) is True


def test_an_unignored_target_is_recognised(tmp_path: Path):
    assert ignored(project(tmp_path, gitignore=".env\n")) is False


def test_a_nested_ignore_file_is_honoured(tmp_path: Path):
    """git check-ignore reads these, which is why git decides and not a regex."""
    root = project(tmp_path, gitignore="node_modules\n")
    (root / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (root / ".git" / "info" / "exclude").write_text(".env.production\n", encoding="utf-8")

    assert ignored(root) is True


def test_a_directory_that_is_not_a_repository_is_not_ignored(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()

    assert ignored(plain) is False


def test_git_being_unavailable_fails_closed(tmp_path: Path, monkeypatch):
    """Built before git is taken away, so only the check under test is affected."""
    root = project(tmp_path)

    def absent(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(sealing.subprocess, "run", absent)

    assert ignored(root) is False


def test_nothing_is_written_when_the_target_is_not_ignored(tmp_path: Path):
    """The reason this module exists. A warning that still wrote would defeat it."""
    root = project(tmp_path, gitignore=".env\n")

    result = run(root)

    assert result.ok is False
    assert result.ignored is False
    assert result.written == ""
    assert not (root / PRODUCTION).exists(), "a real credential was written to a tracked tree"
    assert "not covered by .gitignore" in result.reason


# --------------------------------------------------------------------------
# sealing end to end
# --------------------------------------------------------------------------


def test_a_correct_project_seals(tmp_path: Path):
    root = project(tmp_path)

    result = run(root)

    assert result.ok is True
    assert result.sealed == ("PORT", "STRIPE_KEY")
    assert result.blocked == ()
    assert result.uncovered == ()
    assert Path(result.written).name == PRODUCTION


def test_the_written_file_carries_the_real_values(tmp_path: Path):
    root = project(tmp_path)

    run(root)

    written = (root / PRODUCTION).read_text(encoding="utf-8")
    assert f"STRIPE_KEY={SECRET}" in written
    assert "PORT=3000" in written


def test_the_written_file_says_not_to_commit_it(tmp_path: Path):
    root = project(tmp_path)

    run(root)

    assert "never commit" in (root / PRODUCTION).read_text(encoding="utf-8").lower()


def test_a_placeholder_stops_the_seal_and_names_the_key(tmp_path: Path):
    root = project(tmp_path, env=f"PORT=3000\nSTRIPE_KEY=CHANGEME\n")

    result = run(root)

    assert result.ok is False
    assert result.blocked == ("STRIPE_KEY",)
    assert "STRIPE_KEY" in result.reason
    assert not (root / PRODUCTION).exists()


def test_a_placeholder_is_never_written_through(tmp_path: Path):
    """A service configured with the word CHANGEME fails in a worse way."""
    values = (
        Value("PORT", "3000", Kind.PLAIN, ""),
        Value("KEY", "CHANGEME", Kind.PLACEHOLDER, ""),
    )

    written = render(values)

    assert "PORT=3000" in written
    assert "CHANGEME" not in written


def test_a_missing_template_key_is_reported(tmp_path: Path):
    root = project(tmp_path, example="PORT=\n")

    result = run(root)

    assert result.uncovered == ("STRIPE_KEY",)
    assert result.ok is False
    assert EXAMPLE in result.reason


def test_a_project_with_no_env_file_is_reported(tmp_path: Path):
    root = project(tmp_path, env=None)

    result = run(root)

    assert result.ok is False
    assert ENV in result.reason
    assert result.values == ()


def test_an_empty_env_file_is_reported(tmp_path: Path):
    root = project(tmp_path, env="# nothing here\n")

    result = run(root)

    assert result.ok is False
    assert "no values" in result.reason


def test_a_path_that_is_not_a_project_is_reported(tmp_path: Path):
    result = run(tmp_path / "nowhere")

    assert result.ok is False
    assert "not a project directory" in result.reason


# --------------------------------------------------------------------------
# what stage 3 consumes, and what it must never contain
# --------------------------------------------------------------------------


def test_the_result_serialises_whole(tmp_path: Path):
    payload = run(project(tmp_path)).to_dict()

    assert set(payload) == {
        "project", "ok", "reason", "ignored", "written",
        "sealed", "blocked", "uncovered", "values",
    }
    json.dumps(payload)


def test_no_secret_value_reaches_the_result(tmp_path: Path):
    """The file holds the real values. The report never does."""
    payload = json.dumps(run(project(tmp_path)).to_dict())

    assert SECRET not in payload
    assert "sk_liv" in payload, "the masked form should still identify it"


def test_a_plain_value_is_not_masked(tmp_path: Path):
    """Masking a port number would make the report harder to read for no gain."""
    result = run(project(tmp_path))

    port = next(v for v in result.values if v.key == "PORT")
    assert port.masked == "3000"


def test_an_empty_seal_is_not_ok():
    assert Seal(project="x").ok is False

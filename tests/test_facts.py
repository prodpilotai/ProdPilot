"""The facts collector reads what the artefact says rather than what it is told."""

from __future__ import annotations

from facts import matrix, node_use, version


def test_matrix_reads_the_os_and_python_lists() -> None:
    text = """
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        python: ["3.11", "3.12", "3.13", "3.14"]
    """
    found = matrix(text)
    assert found["os"] == ["ubuntu-latest", "macos-latest", "windows-latest"]
    assert found["python"] == ["3.11", "3.12", "3.13", "3.14"]


def test_matrix_without_a_list_is_empty() -> None:
    assert matrix("jobs:\n  test:\n    runs-on: ubuntu-latest\n") == {}


def test_node_use_counts_images_and_fallbacks() -> None:
    rows = [
        {"image": "node:lts", "fallback": False, "wanted": 24},
        {"image": "node:lts", "fallback": True, "wanted": 18},
        {"image": "node:20", "fallback": False, "wanted": 20},
        {"image": "node:lts", "fallback": True, "wanted": 18},
    ]
    found = node_use(rows)
    assert found["rows"] == 4
    assert found["by_image"] == {"node:lts": 3, "node:20": 1}
    assert found["fallback_rows"] == 2
    assert found["fallback_wanted"] == {"18": 2}


def test_version_reports_both_declarations() -> None:
    found = version()
    assert set(found) == {"pyproject", "runtime", "agree", "tags"}
    assert found["agree"] == (found["pyproject"] == found["runtime"])

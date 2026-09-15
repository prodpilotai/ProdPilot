"""A content fix that reads an environment key keeps ENV-001 passing.

In module 7.3's full-chain run SEC-003's fix made the code read CORS_ORIGIN,
.env.example did not declare it, ENV-001 failed, and the regression guard
reverted the fix on four projects. The contract now names the key, and the
executor, like an agent following the constraint, declares it.
"""

from __future__ import annotations

import json
from pathlib import Path

from apply import write
from prodpilot import audit, dispatch, verify

APP = """\
const express = require("express");
const cors = require("cors");

const app = express();
app.use(cors());

app.get("/health", (req, res) => res.json({ status: "ok" }));

app.listen(process.env.PORT || 3000);
"""


def project(root: Path) -> Path:
    (root / "src").mkdir()
    (root / "package.json").write_text(json.dumps({
        "name": "demo", "version": "1.0.0", "main": "src/server.js",
        "dependencies": {"express": "^4.21.0", "cors": "^2.8.5"},
    }), encoding="utf-8")
    (root / "src" / "server.js").write_text(APP, encoding="utf-8")
    (root / ".env.example").write_text("PORT=\n", encoding="utf-8")
    return root


def fix_cors(root: Path) -> str:
    report = audit.run(str(root))
    return write(root, dispatch.instruct(str(root), dispatch.failing(report, "SEC-003")))


def test_the_cors_fix_leaves_env_001_passing(tmp_path: Path):
    root = project(tmp_path)
    assert verify.check(str(root), "ENV-001").passed
    assert not verify.check(str(root), "SEC-003").passed

    done = fix_cors(root)

    assert "CORS_ORIGIN" in done
    assert (root / ".env.example").read_text(encoding="utf-8") == "PORT=\nCORS_ORIGIN=\n"
    assert verify.check(str(root), "SEC-003").passed
    assert verify.check(str(root), "ENV-001").passed


def test_a_fix_does_not_create_an_env_example_it_does_not_own(tmp_path: Path):
    """Creating the file is ENV-001's own fix, which declares every key the code reads."""
    root = project(tmp_path)
    (root / ".env.example").unlink()

    fix_cors(root)

    assert not (root / ".env.example").exists()
    assert verify.check(str(root), "SEC-003").passed


def test_a_key_already_declared_is_left_alone(tmp_path: Path):
    root = project(tmp_path)
    (root / ".env.example").write_text("PORT=\nCORS_ORIGIN=https://example.com\n", encoding="utf-8")

    fix_cors(root)

    assert (root / ".env.example").read_text(encoding="utf-8") == (
        "PORT=\nCORS_ORIGIN=https://example.com\n")

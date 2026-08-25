"""JavaScript parsing for the audit engine.

Scope is Phase 2 module 2.2. This module turns JavaScript source into an ESTree
tree. It runs no rule checks.

Why acorn and not esprima
-------------------------
Section 9 of the Complete Solution Document names "esprima/acorn". Both were
tested against syntax that real vibe-coded projects contain, and only one of
them holds up from Python.

The esprima Python port (PyPI "esprima", 4.0.1) stops at ECMAScript 2017. It
fails on optional chaining, nullish coalescing, class fields, dynamic import and
JSX fragments. Every one of those is ordinary in code an AI assistant writes
today, so a parser that rejects them would report a syntax error on projects
that are perfectly valid. The "esprima2" fork reaches ES2025 but installs into
the same "esprima" import name as the official package, so whichever was
installed last wins. A dependency that resolves differently depending on install
order is not something to ship.

acorn is the other tool Section 9 names, so this is a choice between the two
options already in the document rather than a substitution. It parses every case
tested, JSX fragments included, and it is the parser eslint, webpack and rollup
are built on.

Acorn is JavaScript, so it runs under Node. That adds no requirement in
practice: Section 9 supports exactly Node.js 20 with Express and React 18 with
Vite, and neither can be built or run without Node. Any project ProdPilot audits
already has it.

acorn and acorn-jsx are vendored under vendor/node_modules so no npm install is
needed and the parse is offline and deterministic.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

VENDOR = Path(__file__).resolve().parent / "vendor"
SCRIPT = VENDOR / "parse.mjs"

# A parse should take milliseconds. This ceiling only exists so a pathological
# file cannot hang the audit.
TIMEOUT = 30


class ParseError(Exception):
    """Raised when the parser could not be run at all.

    A file that fails to parse is not this. That is a normal result carried on
    Tree.ok, because unparseable source is a property of the project under
    audit. This is for a broken toolchain: Node missing, the vendored script
    gone, or the subprocess failing to return usable output.
    """


class NodeMissing(ParseError):
    """Raised when no Node runtime can be found on PATH."""


@dataclass(frozen=True)
class Tree:
    """One parsed source file, or the reason it could not be parsed."""

    ok: bool
    ast: dict | None = None
    source_type: str | None = None
    error: str | None = None
    line: int | None = None
    column: int | None = None
    path: str | None = None

    def body(self) -> list[dict]:
        """Top level statements, or an empty list when the parse failed."""
        if not self.ok or not self.ast:
            return []
        return self.ast.get("body", [])


def node_path() -> str:
    """Locate the Node executable.

    Raises NodeMissing rather than returning None, so a caller cannot proceed
    on the assumption that a parse will work.
    """
    found = shutil.which("node")
    if not found:
        raise NodeMissing(
            "Node is required to parse JavaScript and was not found on PATH. "
            "Both supported stacks need Node to build, so installing Node 20 "
            "or later resolves this."
        )
    return found


def parse(src: str, path: str | None = None) -> Tree:
    """Parse JavaScript source into an ESTree tree.

    Returns a Tree with ok False when the source itself does not parse. Raises
    ParseError when the parser could not be run.
    """
    if not SCRIPT.is_file():
        raise ParseError(f"vendored parser is missing at {SCRIPT}")

    exe = node_path()
    try:
        proc = subprocess.run(
            [exe, str(SCRIPT)],
            input=src,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT,
            cwd=str(VENDOR),
        )
    except subprocess.TimeoutExpired as exc:
        raise ParseError(f"parsing timed out after {TIMEOUT}s") from exc
    except OSError as exc:
        raise ParseError(f"could not run the parser: {exc}") from exc

    if proc.returncode != 0:
        raise ParseError(
            f"parser exited {proc.returncode}: {proc.stderr.strip()[:400]}"
        )

    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ParseError(f"parser returned output that is not JSON: {exc}") from exc

    if raw.get("ok"):
        return Tree(
            ok=True,
            ast=raw.get("ast"),
            source_type=raw.get("sourceType"),
            path=path,
        )

    logger.info("could not parse %s: %s", path or "source", raw.get("error"))
    return Tree(
        ok=False,
        error=raw.get("error"),
        line=raw.get("line"),
        column=raw.get("column"),
        path=path,
    )


def parse_file(path: str | Path) -> Tree:
    """Parse a file from disk.

    A file that cannot be read is reported as an unparseable Tree rather than
    raising, so one unreadable file does not abort a whole audit.
    """
    p = Path(path)
    try:
        src = p.read_text(encoding="utf-8")
    except OSError as exc:
        return Tree(ok=False, error=f"cannot read file: {exc}", path=str(p))
    except UnicodeDecodeError as exc:
        return Tree(ok=False, error=f"file is not valid UTF-8: {exc}", path=str(p))
    return parse(src, path=str(p))


def walk(node: dict | list | None) -> Iterator[dict]:
    """Yield every ESTree node beneath and including this one, depth first."""
    if isinstance(node, list):
        for item in node:
            yield from walk(item)
        return
    if not isinstance(node, dict) or "type" not in node:
        return
    yield node
    for key, value in node.items():
        if key in ("loc", "range", "type", "start", "end"):
            continue
        if isinstance(value, (dict, list)):
            yield from walk(value)


def find(node: dict | list | None, kind: str) -> list[dict]:
    """Every node of a given ESTree type beneath this one."""
    return [n for n in walk(node) if n.get("type") == kind]


def line_of(node: dict | None) -> int | None:
    """Source line a node starts on, when position tracking is present."""
    if not isinstance(node, dict):
        return None
    loc = node.get("loc") or {}
    start = loc.get("start") or {}
    return start.get("line")


def name_of(node: dict | None) -> str | None:
    """Best effort readable name for an identifier or member expression.

    Returns dotted form for members, so app.use reads as "app.use" rather than
    needing the caller to walk the tree itself.
    """
    if not isinstance(node, dict):
        return None
    kind = node.get("type")
    if kind == "Identifier":
        return node.get("name")
    if kind == "Literal":
        value = node.get("value")
        return value if isinstance(value, str) else None
    if kind == "MemberExpression":
        obj = name_of(node.get("object"))
        prop = node.get("property", {})
        key = prop.get("name") if not node.get("computed") else name_of(prop)
        if obj and key:
            return f"{obj}.{key}"
        return key or obj
    if kind == "CallExpression":
        return name_of(node.get("callee"))
    return None


def calls(tree_or_node: Tree | dict, callee: str) -> list[dict]:
    """Every call to a given dotted name, for example "app.use"."""
    root = tree_or_node.ast if isinstance(tree_or_node, Tree) else tree_or_node
    out = []
    for node in walk(root):
        if node.get("type") != "CallExpression":
            continue
        if name_of(node.get("callee")) == callee:
            out.append(node)
    return out


def imports(tree: Tree) -> dict[str, int]:
    """Module specifiers this file brings in, mapped to their line.

    Covers both an ES import declaration and a CommonJS require call, since
    Express projects commonly use the latter.
    """
    found: dict[str, int] = {}
    if not tree.ok:
        return found
    for node in walk(tree.ast):
        kind = node.get("type")
        if kind == "ImportDeclaration":
            src = (node.get("source") or {}).get("value")
            if isinstance(src, str):
                found.setdefault(src, line_of(node) or 0)
        elif kind == "CallExpression" and name_of(node.get("callee")) == "require":
            args = node.get("arguments") or []
            if args and args[0].get("type") == "Literal":
                value = args[0].get("value")
                if isinstance(value, str):
                    found.setdefault(value, line_of(node) or 0)
    return found

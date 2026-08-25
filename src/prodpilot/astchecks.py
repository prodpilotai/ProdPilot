"""AST based rule checks for the audit engine.

Scope is Phase 2 module 2.2. Every check here reads an ESTree tree and reports
whether a rule holds. Nothing is fixed and nothing is scored. Fix generation is
Phase 3 and score computation is module 2.4.

Each check maps to a rule that already exists in the frozen store from module
2.1, by rule_id, and only to rules whose check_type is ast. No rule is defined
here.

Rules covered:

  SEC-002  helmet registered before any route
  SEC-003  CORS origin read from the environment
  API-003  central error handler present and registered last
  STR-001  database calls kept out of controllers
  STR-002  route handlers delegate instead of holding logic

Section 4 lists middleware ordering as a concern but defines no rule of its own
for it. Ordering is carried by the two rules where it actually matters in
Express: helmet has to come before the routes it protects (SEC-002), and the
error handler has to come after them or it never runs (API-003). Both checks
below assert position, not just presence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from prodpilot.jsparse import Tree, calls, find, line_of, name_of, parse_file, walk

logger = logging.getLogger(__name__)

HTTP_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "all"}
)

# Method names that indicate the code is talking to a database directly. Drawn
# from the drivers the supported stack actually uses: mongoose, knex, prisma,
# the node postgres and mysql clients.
DB_METHODS = frozenset({
    "find", "findone", "findbyid", "findbyidandupdate", "findbyidanddelete",
    "findoneandupdate", "findoneanddelete", "insertone", "insertmany",
    "updateone", "updatemany", "deleteone", "deletemany", "aggregate",
    "countdocuments", "save", "create", "bulkwrite", "query", "execute",
})

DB_OBJECTS = frozenset({"db", "prisma", "knex", "pool", "client", "connection", "sequelize"})

SKIP_DIRS = frozenset({"node_modules", "dist", "build", ".git", "coverage", ".next"})
JS_SUFFIXES = frozenset({".js", ".jsx", ".mjs", ".cjs"})


class Status(str, Enum):
    """Outcome of one check against one file."""

    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"
    UNPARSED = "unparsed"


@dataclass(frozen=True)
class Finding:
    """One check result, shaped for module 2.4 to aggregate.

    A finding is always tied to a rule_id and a file. line is the position of
    the violation when there is one to point at. SKIPPED means the rule does
    not apply to this file, so 2.4 should not count it either way. UNPARSED
    means the file could not be read as JavaScript, which is never treated as a
    pass.
    """

    rule_id: str
    status: Status
    file: str
    line: int | None = None
    detail: str = ""

    @property
    def counts(self) -> bool:
        """Whether this finding contributes to a score at all."""
        return self.status in (Status.PASS, Status.FAIL)

    @property
    def passed(self) -> bool:
        return self.status is Status.PASS

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "status": self.status.value,
            "file": self.file,
            "line": self.line,
            "detail": self.detail,
        }


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------


def js_files(root: str | Path) -> list[Path]:
    """Every JavaScript source file under a project, build output excluded."""
    base = Path(root)
    out: list[Path] = []
    for path in base.rglob("*"):
        if path.suffix not in JS_SUFFIXES or not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        out.append(path)
    return sorted(out)


def rel(path: str | Path, root: str | Path) -> str:
    """Project relative path, for reporting."""
    try:
        return str(Path(path).relative_to(Path(root)).as_posix())
    except ValueError:
        return str(path)


def routes(tree: Tree) -> list[dict]:
    """Route registrations, for example app.get or router.post."""
    out = []
    for node in walk(tree.ast):
        if node.get("type") != "CallExpression":
            continue
        callee = node.get("callee") or {}
        if callee.get("type") != "MemberExpression":
            continue
        prop = (callee.get("property") or {}).get("name")
        if not prop or prop.lower() not in HTTP_METHODS:
            continue
        # app.use is middleware, not a route, and app.all is. Distinguish by
        # whether a path literal leads the arguments.
        args = node.get("arguments") or []
        if args and args[0].get("type") == "Literal" and isinstance(args[0].get("value"), str):
            out.append(node)
    return out


def uses(tree: Tree) -> list[dict]:
    """Middleware registrations, any object's use method."""
    out = []
    for node in walk(tree.ast):
        if node.get("type") != "CallExpression":
            continue
        callee = node.get("callee") or {}
        if callee.get("type") == "MemberExpression":
            if (callee.get("property") or {}).get("name") == "use":
                out.append(node)
    return out


def first_line(nodes: list[dict]) -> int | None:
    lines = [line_of(n) for n in nodes if line_of(n) is not None]
    return min(lines) if lines else None


def is_app(tree: Tree) -> bool:
    """Whether this file builds the Express application itself.

    Helmet, CORS and the error handler are registered once on the app, not in
    every router. Without this distinction each router file in a project would
    report three violations it has no way to satisfy, which would bury the real
    findings. A router calls express.Router, an app calls express directly.
    """
    for node in walk(tree.ast):
        if node.get("type") != "CallExpression":
            continue
        if name_of(node.get("callee")) == "express":
            return True
    return False


def db_calls(node: dict | None) -> list[dict]:
    """Calls that look like direct database access."""
    out = []
    for n in walk(node):
        if n.get("type") != "CallExpression":
            continue
        callee = n.get("callee") or {}
        if callee.get("type") != "MemberExpression":
            continue
        prop = (callee.get("property") or {}).get("name")
        if not prop or prop.lower() not in DB_METHODS:
            continue
        obj = name_of(callee.get("object")) or ""
        head = obj.split(".")[0].lower()
        # A capitalised receiver is a mongoose model, User.find. A known driver
        # object is the other common shape, db.query.
        looks_db = head in DB_OBJECTS or (obj[:1].isupper() if obj else False)
        if looks_db:
            out.append(n)
    return out


def handlers(call: dict) -> list[dict]:
    """Callback functions passed to a route registration."""
    out = []
    for arg in call.get("arguments") or []:
        if arg.get("type") in ("ArrowFunctionExpression", "FunctionExpression"):
            out.append(arg)
    return out


def parts(path: str) -> list[str]:
    """Lowercased path segments.

    Matching on segments rather than on a substring, so a directory at the
    project root is recognised. "routes/thing.js" has no leading slash, and a
    substring test for "/routes/" would miss it.
    """
    return [p for p in path.replace("\\", "/").lower().split("/") if p]


def is_controller(path: str) -> bool:
    seg = parts(path)
    return bool(seg) and (
        "controllers" in seg[:-1]
        or "controller" in seg[:-1]
        or seg[-1].endswith("controller.js")
    )


def is_route_file(path: str) -> bool:
    seg = parts(path)
    return bool(seg) and (
        "routes" in seg[:-1]
        or "router" in seg[:-1]
        or seg[-1].endswith(("routes.js", "router.js"))
    )


def env_ref(node: dict | None) -> bool:
    """Whether an expression reads from the environment.

    Covers process.env.X for Node and import.meta.env.X for Vite, including
    when the value is wrapped, for example in a fallback or a template string.
    """
    for n in walk(node):
        dotted = name_of(n) or ""
        if dotted.startswith("process.env") or "import.meta.env" in dotted:
            return True
        if n.get("type") == "MetaProperty":
            return True
    return False


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------


def check_helmet(tree: Tree, path: str) -> Finding:
    """SEC-002: helmet must be registered before any route is defined."""
    rule = "SEC-002"
    if not tree.ok:
        return Finding(rule, Status.UNPARSED, path, tree.line, tree.error or "parse failed")

    if not is_app(tree):
        return Finding(rule, Status.SKIPPED, path, None,
                       "this file does not build the Express app")

    helmet_uses = [u for u in uses(tree) if "helmet" in (str(name_of(u)) + str(
        [name_of(a) for a in (u.get("arguments") or [])]))]
    route_calls = routes(tree)

    if not route_calls and not helmet_uses:
        return Finding(rule, Status.SKIPPED, path, None, "no routes or middleware here")

    if not helmet_uses:
        return Finding(rule, Status.FAIL, path, first_line(route_calls),
                       "routes are defined but helmet is never registered")

    helmet_line = first_line(helmet_uses)
    route_line = first_line(route_calls)
    if route_line is not None and helmet_line is not None and helmet_line > route_line:
        return Finding(rule, Status.FAIL, path, helmet_line,
                       f"helmet is registered at line {helmet_line}, after the first "
                       f"route at line {route_line}")
    return Finding(rule, Status.PASS, path, helmet_line, "helmet is registered before the routes")


def check_cors(tree: Tree, path: str) -> Finding:
    """SEC-003: the CORS origin must come from the environment, not a literal."""
    rule = "SEC-003"
    if not tree.ok:
        return Finding(rule, Status.UNPARSED, path, tree.line, tree.error or "parse failed")

    if not is_app(tree):
        return Finding(rule, Status.SKIPPED, path, None,
                       "this file does not build the Express app")

    cors_calls = calls(tree, "cors")
    if not cors_calls:
        return Finding(rule, Status.FAIL, path, None, "CORS is never configured")

    for call in cors_calls:
        args = call.get("arguments") or []
        line = line_of(call)
        if not args:
            return Finding(rule, Status.FAIL, path, line,
                           "cors() is called with no options, which allows every origin")
        origin = None
        for prop in (args[0].get("properties") or []):
            if (prop.get("key") or {}).get("name") == "origin":
                origin = prop.get("value")
                break
        if origin is None:
            return Finding(rule, Status.FAIL, path, line, "the CORS options set no origin")
        if origin.get("type") == "Literal" and origin.get("value") == "*":
            return Finding(rule, Status.FAIL, path, line, "the CORS origin is the wildcard")
        if not env_ref(origin):
            return Finding(rule, Status.FAIL, path, line,
                           "the CORS origin is hardcoded rather than read from the environment")
    return Finding(rule, Status.PASS, path, line_of(cors_calls[0]),
                   "the CORS origin is read from the environment")


def check_error_mw(tree: Tree, path: str) -> Finding:
    """API-003: a central error handler must exist and be registered last.

    Express identifies an error handler by arity. A middleware function taking
    four arguments is an error handler, and one registered before the routes
    never runs, so position is part of the rule rather than a separate concern.
    """
    rule = "API-003"
    if not tree.ok:
        return Finding(rule, Status.UNPARSED, path, tree.line, tree.error or "parse failed")

    if not is_app(tree):
        return Finding(rule, Status.SKIPPED, path, None,
                       "this file does not build the Express app")

    route_calls = routes(tree)
    if not route_calls:
        return Finding(rule, Status.SKIPPED, path, None, "no routes defined in this file")

    handlers_found = []
    for call in uses(tree):
        for fn in handlers(call):
            if len(fn.get("params") or []) == 4:
                handlers_found.append(call)

    if not handlers_found:
        return Finding(rule, Status.FAIL, path, first_line(route_calls),
                       "no central error handler is registered")

    last_route = max(
        (line_of(r) for r in route_calls if line_of(r) is not None), default=None
    )
    handler_line = first_line(handlers_found)
    if last_route is not None and handler_line is not None and handler_line < last_route:
        return Finding(rule, Status.FAIL, path, handler_line,
                       f"the error handler is registered at line {handler_line}, before the "
                       f"last route at line {last_route}, so it never runs")
    return Finding(rule, Status.PASS, path, handler_line,
                   "the error handler is registered after the routes")


def check_service_layer(tree: Tree, path: str) -> Finding:
    """STR-001: controllers must not talk to the database directly."""
    rule = "STR-001"
    if not is_controller(path):
        return Finding(rule, Status.SKIPPED, path, None, "not a controller file")
    if not tree.ok:
        return Finding(rule, Status.UNPARSED, path, tree.line, tree.error or "parse failed")

    hits = db_calls(tree.ast)
    if hits:
        return Finding(rule, Status.FAIL, path, line_of(hits[0]),
                       f"{len(hits)} database call(s) in a controller, the first at line "
                       f"{line_of(hits[0])}. These belong in a service")
    return Finding(rule, Status.PASS, path, None, "no database calls in this controller")


def check_thin_routes(tree: Tree, path: str) -> Finding:
    """STR-002: route handlers must delegate rather than hold business logic."""
    rule = "STR-002"
    if not is_route_file(path):
        return Finding(rule, Status.SKIPPED, path, None, "not a route file")
    if not tree.ok:
        return Finding(rule, Status.UNPARSED, path, tree.line, tree.error or "parse failed")

    route_calls = routes(tree)
    if not route_calls:
        return Finding(rule, Status.SKIPPED, path, None, "no routes defined in this file")

    for call in route_calls:
        for fn in handlers(call):
            hits = db_calls(fn)
            if hits:
                return Finding(rule, Status.FAIL, path, line_of(hits[0]),
                               "a route handler queries the database directly instead of "
                               "calling a service")
            body = fn.get("body") or {}
            stmts = body.get("body") if body.get("type") == "BlockStatement" else None
            if isinstance(stmts, list) and len(stmts) > 8:
                return Finding(rule, Status.FAIL, path, line_of(fn),
                               f"a route handler holds {len(stmts)} statements of inline "
                               f"logic instead of delegating to a service")
    return Finding(rule, Status.PASS, path, None, "route handlers delegate their work")


CHECKS = {
    "SEC-002": check_helmet,
    "SEC-003": check_cors,
    "API-003": check_error_mw,
    "STR-001": check_service_layer,
    "STR-002": check_thin_routes,
}


def check_file(path: str | Path, root: str | Path | None = None) -> list[Finding]:
    """Run every AST check against one file."""
    p = Path(path)
    name = rel(p, root) if root else str(p)
    tree = parse_file(p)
    return [fn(tree, name) for fn in CHECKS.values()]


def check_project(root: str | Path) -> list[Finding]:
    """Run every AST check across a project.

    Returns findings for every file examined. Aggregation and scoring are
    module 2.4, so nothing is summarised here.
    """
    base = Path(root)
    if not base.is_dir():
        raise NotADirectoryError(f"not a project directory: {base}")
    out: list[Finding] = []
    for path in js_files(base):
        out.extend(check_file(path, base))
    logger.info("ran %s AST checks across %s", len(out), base)
    return out

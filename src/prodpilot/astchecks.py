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
import re
from dataclasses import dataclass
from pathlib import Path

from prodpilot.findings import Finding, Status
from prodpilot.jsparse import (
    Tree,
    calls,
    find,
    imports,
    line_of,
    name_of,
    parse_file,
    walk,
)

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


def strings(node: dict | None) -> list[str]:
    """Every string literal beneath a node."""
    out = []
    for n in walk(node):
        if n.get("type") == "Literal" and isinstance(n.get("value"), str):
            out.append(n["value"])
        elif n.get("type") == "TemplateLiteral":
            for q in n.get("quasis") or []:
                raw = (q.get("value") or {}).get("cooked")
                if isinstance(raw, str) and raw:
                    out.append(raw)
    return out


def jsx_name(node: dict | None) -> str | None:
    """Tag name of a JSX element, for example Route or ErrorBoundary."""
    if not isinstance(node, dict):
        return None
    opening = node.get("openingElement") or {}
    name = opening.get("name") or {}
    if name.get("type") == "JSXIdentifier":
        return name.get("name")
    if name.get("type") == "JSXMemberExpression":
        obj = (name.get("object") or {}).get("name")
        prop = (name.get("property") or {}).get("name")
        return f"{obj}.{prop}" if obj and prop else prop
    return None


def jsx_attr(node: dict, attr: str) -> str | None:
    """Value of a JSX attribute when it is a plain string."""
    opening = node.get("openingElement") or {}
    for a in opening.get("attributes") or []:
        if (a.get("name") or {}).get("name") != attr:
            continue
        value = a.get("value") or {}
        if value.get("type") == "Literal" and isinstance(value.get("value"), str):
            return value["value"]
    return None


def uses_pkg(tree: Tree, *names: str) -> bool:
    """Whether the file imports or requires any of these packages."""
    declared = set(imports(tree))
    for want in names:
        if any(mod == want or mod.startswith(want + "/") for mod in declared):
            return True
    # Some projects reach a package through a member call without importing it
    # at the top, for example helmet.contentSecurityPolicy.
    for node in walk(tree.ast):
        dotted = name_of(node) or ""
        if any(dotted == n or dotted.startswith(n + ".") for n in names):
            return True
    return False


def env_keys(tree: Tree) -> set[str]:
    """Environment variable names the file reads.

    Covers process.env.NAME for Node and import.meta.env.NAME for Vite, plus
    the bracket form process.env["NAME"].
    """
    found: set[str] = set()
    for node in walk(tree.ast):
        if node.get("type") != "MemberExpression":
            continue
        # import.meta.env is built on a MetaProperty, which the dotted name
        # reader renders as plain "env", so match it by shape before falling
        # back to the name.
        inner = node.get("object") or {}
        if (inner.get("type") == "MemberExpression"
                and (inner.get("property") or {}).get("name") == "env"
                and (inner.get("object") or {}).get("type") == "MetaProperty"):
            obj = "import.meta.env"
        else:
            obj = name_of(inner) or ""
        if obj not in ("process.env", "import.meta.env"):
            continue
        prop = node.get("property") or {}
        if node.get("computed"):
            if prop.get("type") == "Literal" and isinstance(prop.get("value"), str):
                found.add(prop["value"])
        elif prop.get("name"):
            found.add(prop["name"])
    return found


# --------------------------------------------------------------------------
# file scoped checks
# --------------------------------------------------------------------------


def check_csp(tree: Tree, path: str) -> Finding:
    """SEC-004: Content Security Policy and an HTTPS redirect must be set."""
    rule = "SEC-004"
    if not tree.ok:
        return Finding(rule, Status.UNPARSED, path, tree.line, tree.error or "parse failed")
    if not is_app(tree):
        return Finding(rule, Status.SKIPPED, path, None, "this file does not build the Express app")

    body = " ".join(strings(tree.ast)).lower()
    dotted = " ".join(str(name_of(n)) for n in walk(tree.ast)).lower()
    # helmet sets CSP by default, so registering it counts unless it is
    # explicitly disabled.
    csp = ("content-security-policy" in body or "contentsecuritypolicy" in dotted
           or uses_pkg(tree, "helmet"))
    if "contentsecuritypolicy: false" in dotted.replace(" ", ""):
        csp = False
    https = ("x-forwarded-proto" in body or "req.secure" in dotted
             or "strict-transport-security" in body or uses_pkg(tree, "express-sslify"))

    if csp and https:
        return Finding(rule, Status.PASS, path, None,
                       "a Content Security Policy and an HTTPS redirect are both set")
    missing = [n for n, ok in (("CSP", csp), ("HTTPS redirect", https)) if not ok]
    return Finding(rule, Status.FAIL, path, None, f"not configured: {', '.join(missing)}")


def declared(tree: Tree, node: dict | None) -> dict | None:
    """The value a plain variable was declared with, or None.

    Lets ENV-002 follow `const port = process.env.PORT || 3000` into
    `app.listen(port)`, the common way to write it, which module 7.3's
    full-chain run found failing as though the port were not read at all.
    """
    if not node or node.get("type") != "Identifier":
        return None
    for n in walk(tree.ast):
        if (n.get("type") == "VariableDeclarator"
                and (n.get("id") or {}).get("name") == node.get("name")):
            return n.get("init")
    return None


def check_port(tree: Tree, path: str) -> Finding:
    """ENV-002: the listening port must come from the environment."""
    rule = "ENV-002"
    if not tree.ok:
        return Finding(rule, Status.UNPARSED, path, tree.line, tree.error or "parse failed")

    listens = [n for n in walk(tree.ast)
               if n.get("type") == "CallExpression"
               and (name_of(n.get("callee")) or "").endswith(".listen")]
    if not listens:
        return Finding(rule, Status.SKIPPED, path, None, "this file starts no server")

    for call in listens:
        args = call.get("arguments") or []
        if not args:
            continue
        if env_ref(args[0]) or env_ref(declared(tree, args[0])):
            return Finding(rule, Status.PASS, path, line_of(call),
                           "the port is read from the environment")
        if args[0].get("type") == "Literal":
            return Finding(rule, Status.FAIL, path, line_of(call),
                           f"the port is hardcoded as {args[0].get('value')}")
    return Finding(rule, Status.FAIL, path, line_of(listens[0]),
                   "the listening port does not come from the environment")


DB_CONNECTS = ("mongoose.connect", "createConnection", "createPool", "connect")
POOL_NAMES = ("Pool", "createPool", "poolSize", "maxPoolSize", "connectionLimit", "max")


def db_connects(tree: Tree) -> list[dict]:
    """Calls that open a database connection."""
    out = []
    for node in walk(tree.ast):
        if node.get("type") == "NewExpression":
            if (name_of(node.get("callee")) or "") in ("Pool", "Client", "Sequelize"):
                out.append(node)
            continue
        if node.get("type") != "CallExpression":
            continue
        dotted = name_of(node.get("callee")) or ""
        if any(dotted == c or dotted.endswith("." + c) for c in DB_CONNECTS):
            out.append(node)
    return out


def check_db_env(tree: Tree, path: str) -> Finding:
    """CON-001: database credentials must come from the environment."""
    rule = "CON-001"
    if not tree.ok:
        return Finding(rule, Status.UNPARSED, path, tree.line, tree.error or "parse failed")

    conns = db_connects(tree)
    if not conns:
        return Finding(rule, Status.SKIPPED, path, None, "this file opens no database connection")

    for call in conns:
        for text in strings(call):
            if "://" in text and "@" in text:
                return Finding(rule, Status.FAIL, path, line_of(call),
                               "the connection string carries inline credentials")
        for arg in call.get("arguments") or []:
            for prop in (arg.get("properties") or []):
                key = (prop.get("key") or {}).get("name")
                if key in ("password", "user", "username") and not env_ref(prop.get("value")):
                    return Finding(rule, Status.FAIL, path, line_of(call),
                                   f"{key} is hardcoded rather than read from the environment")
    if any(env_ref(c) for c in conns):
        return Finding(rule, Status.PASS, path, line_of(conns[0]),
                       "database credentials are read from the environment")
    return Finding(rule, Status.FAIL, path, line_of(conns[0]),
                   "the connection does not read credentials from the environment")


def check_pool(tree: Tree, path: str) -> Finding:
    """CON-002: database access must go through a connection pool."""
    rule = "CON-002"
    if not tree.ok:
        return Finding(rule, Status.UNPARSED, path, tree.line, tree.error or "parse failed")

    conns = db_connects(tree)
    if not conns:
        return Finding(rule, Status.SKIPPED, path, None, "this file opens no database connection")

    dotted = {name_of(n) for n in walk(tree.ast)}
    keys = {(p.get("key") or {}).get("name")
            for n in walk(tree.ast) if n.get("type") == "ObjectExpression"
            for p in (n.get("properties") or [])}
    # mongoose pools by default, so its presence satisfies the rule.
    if uses_pkg(tree, "mongoose"):
        return Finding(rule, Status.PASS, path, None, "mongoose pools connections by default")
    if any(n in dotted for n in ("Pool", "createPool")) or (set(POOL_NAMES) & keys):
        return Finding(rule, Status.PASS, path, None, "a connection pool is configured")
    return Finding(rule, Status.FAIL, path, line_of(conns[0]),
                   "a single client is opened with no pool, so every request pays a new connection")


def check_rate_limit(tree: Tree, path: str) -> Finding:
    """API-001: public routes must be rate limited."""
    rule = "API-001"
    if not tree.ok:
        return Finding(rule, Status.UNPARSED, path, tree.line, tree.error or "parse failed")
    if not is_app(tree):
        return Finding(rule, Status.SKIPPED, path, None, "this file does not build the Express app")

    if uses_pkg(tree, "express-rate-limit", "express-slow-down", "rate-limiter-flexible"):
        return Finding(rule, Status.PASS, path, None, "a rate limiter is registered")
    dotted = {name_of(n) for n in walk(tree.ast)}
    if {"rateLimit", "rateLimiter", "limiter"} & dotted:
        return Finding(rule, Status.PASS, path, None, "a rate limiter is registered")
    return Finding(rule, Status.FAIL, path, None,
                   "no rate limiting, so any client can call the API without bound")


def check_health(tree: Tree, path: str) -> Finding:
    """OBS-001: a health check endpoint must be exposed."""
    rule = "OBS-001"
    if not tree.ok:
        return Finding(rule, Status.UNPARSED, path, tree.line, tree.error or "parse failed")
    if not is_app(tree):
        return Finding(rule, Status.SKIPPED, path, None, "this file does not build the Express app")

    route_calls = routes(tree)
    if not route_calls:
        return Finding(rule, Status.SKIPPED, path, None, "no routes defined in this file")

    for call in route_calls:
        args = call.get("arguments") or []
        target = str(args[0].get("value", "")).lower() if args else ""
        if any(word in target for word in ("health", "healthz", "readyz", "livez")):
            return Finding(rule, Status.PASS, path, line_of(call),
                           f"a health endpoint is exposed at {args[0].get('value')}")
    return Finding(rule, Status.FAIL, path, first_line(route_calls),
                   "no health endpoint, so the platform cannot tell whether the service is up")


LOGGERS = ("winston", "pino", "bunyan", "morgan", "loglevel", "@logtail/node")


def check_logger(tree: Tree, path: str) -> Finding:
    """OBS-002: structured logging must replace bare console output."""
    rule = "OBS-002"
    if not tree.ok:
        return Finding(rule, Status.UNPARSED, path, tree.line, tree.error or "parse failed")
    if not is_app(tree):
        return Finding(rule, Status.SKIPPED, path, None, "this file does not build the Express app")

    if uses_pkg(tree, *LOGGERS):
        return Finding(rule, Status.PASS, path, None, "a structured logger is configured")
    # Projects normally configure the logger in its own module and import it
    # from there, so a local logger import counts as evidence.
    local = [m for m in imports(tree) if m.startswith(".") and "logger" in m.lower()]
    if local:
        return Finding(rule, Status.PASS, path, None, f"a logger is imported from {local[0]}")
    console = [n for n in walk(tree.ast)
               if (name_of(n.get("callee")) or "").startswith("console.")]
    detail = "no structured logger is configured"
    if console:
        detail += f", and {len(console)} bare console call(s) are used instead"
    return Finding(rule, Status.FAIL, path, line_of(console[0]) if console else None, detail)


def check_metrics(tree: Tree, path: str) -> Finding:
    """OBS-003: the process must expose monitoring hooks."""
    rule = "OBS-003"
    if not tree.ok:
        return Finding(rule, Status.UNPARSED, path, tree.line, tree.error or "parse failed")
    if not is_app(tree):
        return Finding(rule, Status.SKIPPED, path, None, "this file does not build the Express app")

    if uses_pkg(tree, "prom-client", "express-prom-bundle", "@opentelemetry/api"):
        return Finding(rule, Status.PASS, path, None, "a metrics client is registered")
    for call in routes(tree):
        args = call.get("arguments") or []
        target = str(args[0].get("value", "")).lower() if args else ""
        if "metrics" in target:
            return Finding(rule, Status.PASS, path, line_of(call),
                           "a metrics endpoint is exposed")
    return Finding(rule, Status.FAIL, path, None,
                   "no monitoring hooks, so the platform has nothing to scrape")


def check_sigterm(tree: Tree, path: str) -> Finding:
    """OBS-004: SIGTERM must be handled so connections drain before exit."""
    rule = "OBS-004"
    if not tree.ok:
        return Finding(rule, Status.UNPARSED, path, tree.line, tree.error or "parse failed")
    if not is_app(tree):
        return Finding(rule, Status.SKIPPED, path, None, "this file does not build the Express app")

    for node in walk(tree.ast):
        if name_of(node.get("callee")) != "process.on":
            continue
        args = node.get("arguments") or []
        if args and str(args[0].get("value", "")).upper() == "SIGTERM":
            return Finding(rule, Status.PASS, path, line_of(node), "SIGTERM is handled")
    return Finding(rule, Status.FAIL, path, None,
                   "SIGTERM is not handled, so a redeploy cuts live requests")


def check_catch_all(tree: Tree, path: str) -> Finding:
    """STR-004: the client router must handle unmatched routes."""
    rule = "STR-004"
    if not tree.ok:
        return Finding(rule, Status.UNPARSED, path, tree.line, tree.error or "parse failed")

    jsx_routes = [n for n in find(tree.ast, "JSXElement") if jsx_name(n) == "Route"]
    # A router config object pairs a path with what to render. Matching on the
    # path key alone would catch any object that happens to carry one, which in
    # an Express file is common.
    obj_routes = []
    for n in walk(tree.ast):
        if n.get("type") != "ObjectExpression":
            continue
        keys = {(p.get("key") or {}).get("name") for p in (n.get("properties") or [])}
        if "path" in keys and keys & {"element", "component", "Component", "children"}:
            obj_routes.append(n)
    if not jsx_routes and not obj_routes:
        return Finding(rule, Status.SKIPPED, path, None, "no client routes defined in this file")

    for node in jsx_routes:
        target = jsx_attr(node, "path")
        if target in ("*", "/*"):
            return Finding(rule, Status.PASS, path, line_of(node),
                           "unmatched routes fall through to a catch all")
    for node in obj_routes:
        for prop in node.get("properties") or []:
            if (prop.get("key") or {}).get("name") != "path":
                continue
            value = (prop.get("value") or {}).get("value")
            if value in ("*", "/*"):
                return Finding(rule, Status.PASS, path, line_of(node),
                               "unmatched routes fall through to a catch all")
    return Finding(rule, Status.FAIL, path,
                   first_line(jsx_routes) or first_line(obj_routes),
                   "no catch all route, so an unknown path renders nothing")


# --------------------------------------------------------------------------
# project scoped checks
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Sources:
    """Every parsed source in a project, plus the env template.

    Cross-file rules cannot be decided from one file, so the trees are parsed
    once here and shared rather than reparsed per rule.
    """

    root: Path
    trees: tuple[tuple[str, Tree], ...]
    env_example: str | None

    def ok_trees(self) -> list[tuple[str, Tree]]:
        return [(name, t) for name, t in self.trees if t.ok]

    @property
    def unparsed(self) -> list[str]:
        return [name for name, t in self.trees if not t.ok]


def read_env(root: Path) -> str | None:
    for name in (".env.example", ".env.sample", ".env.template"):
        path = root / name
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return None


def declared_keys(text: str | None) -> set[str]:
    """Keys an env template declares."""
    if not text:
        return set()
    out = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        out.add(line.split("=", 1)[0].strip())
    return out


def load_sources(root: str | Path) -> Sources:
    """Parse every JavaScript file in a project once."""
    base = Path(root)
    trees = tuple((rel(p, base), parse_file(p)) for p in js_files(base))
    return Sources(root=base, trees=trees, env_example=read_env(base))


def env_coverage(src: Sources, rule: str, prefix: str | None) -> Finding:
    """Shared body for ENV-001 and ENV-003.

    prefix limits which keys are required, since Vite only exposes names
    beginning VITE_ to client code.
    """
    used: dict[str, str] = {}
    for name, tree in src.ok_trees():
        for key in env_keys(tree):
            if prefix and not key.startswith(prefix):
                continue
            used.setdefault(key, name)

    if not used:
        return Finding(rule, Status.SKIPPED, ".env.example", None,
                       "the code reads no environment variables")
    if src.env_example is None:
        return Finding(rule, Status.FAIL, ".env.example", None,
                       f"no .env.example, but the code reads {len(used)} environment key(s)")

    missing = sorted(k for k in used if k not in declared_keys(src.env_example))
    if missing:
        shown = ", ".join(missing[:5]) + (" and more" if len(missing) > 5 else "")
        return Finding(rule, Status.FAIL, ".env.example", None,
                       f"{len(missing)} key(s) read by the code are not declared: {shown}")
    return Finding(rule, Status.PASS, ".env.example", None,
                   f"all {len(used)} environment key(s) are declared")


def check_env_node(src: Sources, rule: str) -> Finding:
    """ENV-001: .env.example must cover every key the code reads."""
    return env_coverage(src, rule, prefix=None)


def check_env_vite(src: Sources, rule: str) -> Finding:
    """ENV-003: .env.example must cover every VITE_ key the code reads."""
    return env_coverage(src, rule, prefix="VITE_")


HOST_RE = re.compile(r"https?://(?!localhost|127\.0\.0\.1|0\.0\.0\.0)[A-Za-z0-9.\-]+")


def check_api_url(src: Sources, rule: str) -> Finding:
    """ENV-004: the API base URL must come from import.meta.env."""
    literals: list[tuple[str, int, str]] = []
    for name, tree in src.ok_trees():
        for node in walk(tree.ast):
            if node.get("type") != "CallExpression":
                continue
            callee = name_of(node.get("callee")) or ""
            if callee != "fetch" and not callee.startswith("axios"):
                continue
            for text in strings(node):
                if HOST_RE.match(text):
                    literals.append((name, line_of(node) or 0, text))

    reads_env = any("VITE_" in k for _, t in src.ok_trees() for k in env_keys(t))
    if literals:
        name, line, text = literals[0]
        return Finding(rule, Status.FAIL, name, line,
                       f"a request targets the literal {text}, not import.meta.env")
    if reads_env:
        return Finding(rule, Status.PASS, ".", None,
                       "request targets come from import.meta.env")
    return Finding(rule, Status.SKIPPED, ".", None, "this project makes no outbound requests")


def check_host_literals(src: Sources, rule: str) -> Finding:
    """ENV-005: no backend host literal may appear in source."""
    hits: list[tuple[str, int, str]] = []
    for name, tree in src.ok_trees():
        for node in walk(tree.ast):
            if node.get("type") != "Literal" or not isinstance(node.get("value"), str):
                continue
            text = node["value"]
            if HOST_RE.match(text) and len(text) > 12:
                hits.append((name, line_of(node) or 0, text))

    if not hits:
        return Finding(rule, Status.PASS, ".", None, "no backend host literals in source")
    name, line, text = hits[0]
    return Finding(rule, Status.FAIL, name, line,
                   f"{len(hits)} host literal(s) in source, the first {text}")


VERSION_RE = re.compile(r"^/?(api/)?v\d+(/|$)", re.IGNORECASE)


def check_versioning(src: Sources, rule: str) -> Finding:
    """API-002: routes must be mounted under a versioned prefix."""
    mounts: list[tuple[str, int, str]] = []
    for name, tree in src.ok_trees():
        if not is_app(tree):
            continue
        for call in uses(tree) + routes(tree):
            args = call.get("arguments") or []
            if not args or args[0].get("type") != "Literal":
                continue
            target = args[0].get("value")
            if isinstance(target, str) and target.startswith("/"):
                mounts.append((name, line_of(call) or 0, target))

    if not mounts:
        return Finding(rule, Status.SKIPPED, ".", None, "no mounted paths found")
    for name, line, target in mounts:
        stripped = target.lstrip("/")
        if VERSION_RE.match(stripped) or re.search(r"/v\d+(/|$)", target):
            return Finding(rule, Status.PASS, name, line,
                           f"routes are mounted under {target}")
    name, line, target = mounts[0]
    return Finding(rule, Status.FAIL, name, line,
                   f"no versioned prefix, the first mount is {target}")


BOUNDARY_HOOKS = ("componentDidCatch", "getDerivedStateFromError")


def check_boundary(src: Sources, rule: str) -> Finding:
    """STR-003: an error boundary must wrap the root of the component tree."""
    defined = None
    for name, tree in src.ok_trees():
        for node in find(tree.ast, "ClassDeclaration"):
            body = (node.get("body") or {}).get("body") or []
            hooks = {(m.get("key") or {}).get("name") for m in body}
            if hooks & set(BOUNDARY_HOOKS):
                defined = (name, (node.get("id") or {}).get("name") or "the boundary")
                break
        if defined:
            break

    roots = []
    for name, tree in src.ok_trees():
        for node in walk(tree.ast):
            dotted = name_of(node.get("callee")) or ""
            if dotted.endswith("createRoot") or dotted.endswith("ReactDOM.render"):
                roots.append((name, tree, node))

    if not roots:
        return Finding(rule, Status.SKIPPED, ".", None, "this project mounts no React root")
    if defined is None:
        name, _, node = roots[0]
        return Finding(rule, Status.FAIL, name, line_of(node),
                       "no error boundary is defined, so a render error blanks the page")

    boundary = defined[1]
    for name, tree, _ in roots:
        rendered = {jsx_name(n) for n in find(tree.ast, "JSXElement")}
        if boundary in rendered or any("boundary" in str(r).lower() for r in rendered):
            return Finding(rule, Status.PASS, name, None,
                           f"{boundary} wraps the root of the component tree")
    name, _, node = roots[0]
    return Finding(rule, Status.FAIL, name, line_of(node),
                   f"{boundary} is defined in {defined[0]} but does not wrap the root")


# --------------------------------------------------------------------------
# rule wiring
# --------------------------------------------------------------------------

# Rules decided by one file on its own.
CHECKS = {
    "SEC-002": check_helmet,
    "SEC-003": check_cors,
    "SEC-004": check_csp,
    "ENV-002": check_port,
    "CON-001": check_db_env,
    "CON-002": check_pool,
    "API-001": check_rate_limit,
    "API-003": check_error_mw,
    "STR-001": check_service_layer,
    "STR-002": check_thin_routes,
    "STR-004": check_catch_all,
    "OBS-001": check_health,
    "OBS-002": check_logger,
    "OBS-003": check_metrics,
    "OBS-004": check_sigterm,
}

# Rules that need the whole project, matching their cross_file scope in the
# frozen store. One verdict per project rather than one per file.
PROJECT_CHECKS = {
    "ENV-001": check_env_node,
    "ENV-003": check_env_vite,
    "ENV-004": check_api_url,
    "ENV-005": check_host_literals,
    "API-002": check_versioning,
    "STR-003": check_boundary,
}


def check_file(path: str | Path, root: str | Path | None = None) -> list[Finding]:
    """Run every file scoped AST check against one file."""
    p = Path(path)
    name = rel(p, root) if root else str(p)
    tree = parse_file(p)
    return [fn(tree, name) for fn in CHECKS.values()]


def check_project(root: str | Path) -> list[Finding]:
    """Run every AST check across a project.

    File scoped rules produce one finding per file. Cross-file rules produce
    one finding for the project. Aggregation and scoring are module 2.4, so
    nothing is summarised here.
    """
    base = Path(root)
    if not base.is_dir():
        raise NotADirectoryError(f"not a project directory: {base}")

    src = load_sources(base)
    out: list[Finding] = []
    for name, tree in src.trees:
        out.extend(fn(tree, name) for fn in CHECKS.values())
    out.extend(fn(src, rule_id) for rule_id, fn in PROJECT_CHECKS.items())
    logger.info("ran %s AST checks across %s", len(out), base)
    return out

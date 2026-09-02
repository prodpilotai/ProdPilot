"""STATIC fix templates for the bounded agentic loop.

Scope is Phase 3 module 3.2. This module holds the fixed template dictionary for
every rule the frozen store classifies STATIC, and renders each into the first
fix instruction contract form from Section 5.2.

It produces no DYNAMIC-PARAMETRIC extraction (3.3), no DYNAMIC-DELEGATED
constraint (3.4), no MCP wiring (3.5) and no verification (3.6).

Zero variance by construction
-----------------------------
Section 4.1 defines a STATIC rule as one whose correct fix is identical for
every codebase, resolved by a fixed dictionary lookup. So every template here is
a constant. There is no branching, no formatting against project values, and
nothing is read from the target codebase to build content. Calling a template
twice returns the same bytes, which a test asserts rather than assumes.

Where file_path comes from
--------------------------
The Section 5.2 contract carries a file_path, and a template cannot hold one for
a code rule since the file differs per project. It is not derived here either.
It arrives on the audit finding, which Phase 2 already produced, so rendering an
instruction consumes a result that exists rather than inspecting the project
again. A template that targets a fixed path, such as .dockerignore, carries that
path itself and ignores the finding.

That split is the boundary between this module and 3.3. Deciding where a fix
goes from an existing audit result is not extraction. Reading the codebase to
work out what the content should say would be, and none of that happens here.

Anchors
-------
The contract allows an exact existing line or an AST locator. A fixed line
cannot be promised across projects, so these templates use a small set of named
locators, listed in ANCHORS below. They are constants, not values read from a
project, and they say precisely where the content attaches.

The receiver name assumption
----------------------------
Middleware registrations assume the Express application is bound to app, which
is the near universal convention and the one Section 4.1 relies on when it names
helmet registration as a STATIC example. A project binding it to another name
would receive content that does not apply. That is a real limit of classifying
code insertion as STATIC, and the honest place for such a project is
DYNAMIC-PARAMETRIC treatment in 3.3. It is recorded here rather than hidden.

Modules are required inline, for example require("helmet")(), rather than as a
separate import line. A template cannot know whether the module is already
imported, and emitting a second const declaration for a name already bound is a
redeclaration error. An inline require is valid, cannot collide, and keeps the
insertion to a single self contained block.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from prodpilot.audit import RuleResult
from prodpilot.findings import Finding
from prodpilot.rules import FixType, Rule, get_rule

logger = logging.getLogger(__name__)

CONSTRAINT = "Apply exactly this change. Make no other modifications."


class Action(str, Enum):
    """The three actions Section 5.2 allows."""

    CREATE_FILE = "create_file"
    INSERT_AFTER = "insert_after"
    REPLACE_BLOCK = "replace_block"


# Named locators, used where no literal line can be promised across projects.
ANCHORS = {
    "file:end": "the end of the file",
    "package:root": "inside the top level object of package.json",
    "package:scripts": "inside the scripts object of package.json",
    "docker:before-cmd": "immediately before the CMD or ENTRYPOINT instruction",
    "express:before-routes": "after the app is created and before the first route",
    "express:after-routes": "after the last route registration",
    "express:after-listen": "after the call that starts the server",
    "express:listen-call": "the existing call that starts the server",
    "nginx:server": "inside the server block",
    "nginx:root-location": "the location block that serves the application root",
}


class TemplateError(Exception):
    """Raised when a template is asked for a rule it does not cover."""


@dataclass(frozen=True)
class Template:
    """One fixed fix, invariant across every codebase."""

    template_id: str
    action: Action
    anchor: str
    content: str
    rationale: str
    path: str | None = None

    def __post_init__(self) -> None:
        if self.action is Action.CREATE_FILE and not self.path:
            raise TemplateError(f"{self.template_id}: create_file needs a fixed path")
        if self.anchor not in ANCHORS and self.action is not Action.CREATE_FILE:
            raise TemplateError(f"{self.template_id}: unknown anchor {self.anchor}")


@dataclass(frozen=True)
class Instruction:
    """The Section 5.2 contract, first form."""

    rule_id: str
    action: Action
    file_path: str
    anchor: str
    content: str
    rationale: str
    constraint: str = CONSTRAINT

    def to_dict(self) -> dict[str, str]:
        return {
            "rule_id": self.rule_id,
            "action": self.action.value,
            "file_path": self.file_path,
            "anchor": self.anchor,
            "content": self.content,
            "rationale": self.rationale,
            "constraint": self.constraint,
        }


# --------------------------------------------------------------------------
# file bodies, kept as constants so a template is a lookup and nothing more
# --------------------------------------------------------------------------

NODE_DOCKERIGNORE = """\
node_modules
npm-debug.log*
.git
.gitignore
.env
.env.*
!.env.example
coverage
dist
.vscode
Dockerfile
.dockerignore
README.md
"""

REACT_DOCKERIGNORE = """\
node_modules
npm-debug.log*
.git
.gitignore
.env
.env.*
!.env.example
coverage
dist
build
.vscode
Dockerfile
.dockerignore
README.md
"""

NODE_GITIGNORE = """\
node_modules/

.env
.env.*
!.env.example

coverage/
*.log
.DS_Store
"""

REACT_GITIGNORE = """\
node_modules/
dist/
build/

.env
.env.*
!.env.example

coverage/
*.log
.DS_Store
"""

NGINX_CONF = """\
server {
    listen 8080;
    server_name _;
    root /usr/share/nginx/html;

    access_log /dev/stdout;
    error_log /dev/stderr warn;

    add_header Content-Security-Policy "default-src 'self'" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "no-referrer" always;

    location /health {
        access_log off;
        add_header Content-Type text/plain;
        return 200 "ok";
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
"""

ERROR_MIDDLEWARE = """\
app.use((err, req, res, next) => {
  const status = err.status || 500;
  res.status(status).json({ error: { message: "Request failed", status } });
});
"""

GRACEFUL_SHUTDOWN = """\
process.on("SIGTERM", () => {
  server.close(() => {
    process.exit(0);
  });
});
"""

CSP_HEADERS = """\
app.use(require("helmet").contentSecurityPolicy());
app.use(require("helmet").hsts({ maxAge: 31536000 }));
app.use((req, res, next) => {
  if (req.headers["x-forwarded-proto"] === "http") {
    return res.redirect(301, "https://" + req.headers.host + req.originalUrl);
  }
  next();
});
"""

METRICS_HOOKS = """\
const promClient = require("prom-client");
promClient.collectDefaultMetrics();
app.get("/metrics", async (req, res) => {
  res.set("Content-Type", promClient.register.contentType);
  res.end(await promClient.register.metrics());
});
"""


def tpl(
    template_id: str,
    action: Action,
    anchor: str,
    content: str,
    rationale: str,
    path: str | None = None,
) -> Template:
    return Template(template_id, action, anchor, content, rationale, path)


# --------------------------------------------------------------------------
# the dictionary, one entry per STATIC rule
# --------------------------------------------------------------------------

TEMPLATES: dict[str, Template] = {
    # ---- Node, files and configuration ----
    "BLD-002": tpl(
        "tpl.node.dockerignore", Action.CREATE_FILE, "", NODE_DOCKERIGNORE,
        "Keeps dependencies, git metadata and environment files out of the image.",
        path=".dockerignore",
    ),
    "GIT-001": tpl(
        "tpl.node.gitignore", Action.CREATE_FILE, "", NODE_GITIGNORE,
        "Keeps dependencies, build output and environment files out of the repository.",
        path=".gitignore",
    ),
    "GIT-002": tpl(
        "tpl.node.gitignore_node_modules", Action.INSERT_AFTER, "file:end",
        "node_modules/\n",
        "Dependencies are reinstalled from the manifest and must not be committed.",
        path=".gitignore",
    ),
    "SCR-001": tpl(
        "tpl.node.gitignore_env", Action.INSERT_AFTER, "file:end",
        ".env\n.env.production\n!.env.example\n",
        "Environment files carry real credentials and must never be committed.",
        path=".gitignore",
    ),
    "BLD-005": tpl(
        "tpl.node.engine_pin", Action.INSERT_AFTER, "package:root",
        '"engines": {\n    "node": ">=20 <21"\n  },\n',
        "Pins the runtime to Node 20 LTS so the platform cannot pick another version.",
        path="package.json",
    ),
    "SEC-001": tpl(
        "tpl.node.non_root_user", Action.INSERT_AFTER, "docker:before-cmd",
        "USER node\n",
        "Runs the process as an unprivileged user so a compromise cannot own the container.",
        path="Dockerfile",
    ),

    # ---- Node, code ----
    "SEC-002": tpl(
        "tpl.node.helmet_registered", Action.INSERT_AFTER, "express:before-routes",
        'app.use(require("helmet")());\n',
        "Registers security headers before any route so every response carries them.",
    ),
    "SEC-003": tpl(
        "tpl.node.cors_from_env", Action.INSERT_AFTER, "express:before-routes",
        'app.use(require("cors")({ origin: process.env.CORS_ORIGIN }));\n',
        "Reads the allowed origin from the environment instead of trusting every caller.",
    ),
    "SEC-004": tpl(
        "tpl.node.csp_headers", Action.INSERT_AFTER, "express:before-routes",
        CSP_HEADERS,
        "Sets a Content Security Policy, enables HSTS and redirects plain HTTP to HTTPS.",
    ),
    "ENV-002": tpl(
        "tpl.node.port_from_env", Action.REPLACE_BLOCK, "express:listen-call",
        "app.listen(process.env.PORT);\n",
        "Reads the listening port from the environment, which is how the platform assigns it.",
    ),
    "API-001": tpl(
        "tpl.node.rate_limiting", Action.INSERT_AFTER, "express:before-routes",
        'app.use(require("express-rate-limit")({ windowMs: 60000, max: 100 }));\n',
        "Bounds how often a single client can call the API.",
    ),
    "API-003": tpl(
        "tpl.node.error_middleware", Action.INSERT_AFTER, "express:after-routes",
        ERROR_MIDDLEWARE,
        "Returns one consistent error shape and never leaks a stack trace to a caller.",
    ),
    "OBS-001": tpl(
        "tpl.node.health_endpoint", Action.INSERT_AFTER, "express:before-routes",
        'app.get("/health", (req, res) => res.status(200).json({ status: "ok" }));\n',
        "Gives the platform a cheap endpoint to confirm the service is running.",
    ),
    "OBS-002": tpl(
        "tpl.node.structured_logging", Action.INSERT_AFTER, "express:before-routes",
        'app.use(require("pino-http")());\n',
        "Emits structured request logs that a log platform can parse.",
    ),
    "OBS-003": tpl(
        "tpl.node.monitoring_hooks", Action.INSERT_AFTER, "express:before-routes",
        METRICS_HOOKS,
        "Exposes process and request metrics for the platform to scrape.",
    ),
    "OBS-004": tpl(
        "tpl.node.graceful_shutdown", Action.INSERT_AFTER, "express:after-listen",
        GRACEFUL_SHUTDOWN,
        "Drains in flight requests on SIGTERM so a redeploy does not cut live traffic.",
    ),

    # ---- React, files and configuration ----
    "BLD-008": tpl(
        "tpl.react.dockerignore", Action.CREATE_FILE, "", REACT_DOCKERIGNORE,
        "Keeps dependencies, build output and environment files out of the image.",
        path=".dockerignore",
    ),
    "BLD-009": tpl(
        "tpl.react.nginx_conf", Action.CREATE_FILE, "", NGINX_CONF,
        "Serves the built assets with SPA routing, a health path, logging and security headers.",
        path="nginx.conf",
    ),
    "GIT-004": tpl(
        "tpl.react.gitignore", Action.CREATE_FILE, "", REACT_GITIGNORE,
        "Keeps dependencies, build output and environment files out of the repository.",
        path=".gitignore",
    ),
    "GIT-005": tpl(
        "tpl.react.gitignore_node_modules", Action.INSERT_AFTER, "file:end",
        "node_modules/\n",
        "Dependencies are reinstalled from the manifest and must not be committed.",
        path=".gitignore",
    ),
    "GIT-006": tpl(
        "tpl.react.gitignore_dist", Action.INSERT_AFTER, "file:end",
        "dist/\nbuild/\n",
        "Build output is regenerated on every build and must not be committed.",
        path=".gitignore",
    ),
    "SCR-003": tpl(
        "tpl.react.gitignore_env", Action.INSERT_AFTER, "file:end",
        ".env\n.env.production\n!.env.example\n",
        "Environment files carry real credentials and must never be committed.",
        path=".gitignore",
    ),
    "BLD-011": tpl(
        "tpl.react.build_script", Action.INSERT_AFTER, "package:scripts",
        '"build": "vite build",\n',
        "Declares the command that produces the production bundle.",
        path="package.json",
    ),
    "BLD-013": tpl(
        "tpl.react.spa_fallback", Action.REPLACE_BLOCK, "nginx:root-location",
        "location / {\n        try_files $uri $uri/ /index.html;\n    }\n",
        "Falls back to index.html so a client side route resolves on refresh.",
        path="nginx.conf",
    ),
    "SEC-005": tpl(
        "tpl.react.non_root_user", Action.INSERT_AFTER, "docker:before-cmd",
        "USER nginx\n",
        "Runs the server as an unprivileged user so a compromise cannot own the container.",
        path="Dockerfile",
    ),
    "OBS-005": tpl(
        "tpl.react.health_path", Action.INSERT_AFTER, "nginx:server",
        'location /health {\n        access_log off;\n'
        "        add_header Content-Type text/plain;\n"
        '        return 200 "ok";\n    }\n',
        "Serves a health path the platform can probe without loading the application bundle.",
        path="nginx.conf",
    ),
    "OBS-006": tpl(
        "tpl.react.access_logging", Action.INSERT_AFTER, "nginx:server",
        "access_log /dev/stdout;\n    error_log /dev/stderr warn;\n",
        "Writes access and error logs to the container log stream where the platform reads them.",
        path="nginx.conf",
    ),
    "SEC-006": tpl(
        "tpl.react.security_headers", Action.INSERT_AFTER, "nginx:server",
        'add_header Content-Security-Policy "default-src \'self\'" always;\n'
        '    add_header X-Content-Type-Options "nosniff" always;\n'
        '    add_header X-Frame-Options "DENY" always;\n'
        '    add_header Referrer-Policy "no-referrer" always;\n',
        "Sets a Content Security Policy and related headers on every served response.",
        path="nginx.conf",
    ),
}


# --------------------------------------------------------------------------
# lookup and rendering
# --------------------------------------------------------------------------


def covers(rule_id: str) -> bool:
    """Whether this module has a template for a rule."""
    return rule_id in TEMPLATES


def get(rule_id: str) -> Template:
    """The template for a rule.

    Raises rather than returning None. A STATIC issue reaching the loop with no
    template is a gap in the dictionary, not a condition to handle quietly.
    """
    found = TEMPLATES.get(rule_id)
    if found is None:
        raise TemplateError(f"no STATIC template for {rule_id}")
    return found


def target(template: Template, where: Finding | None) -> str:
    """The file the fix applies to.

    A template with a fixed path owns it. Otherwise the path comes from the
    audit finding, which already recorded where the rule failed.
    """
    if template.path:
        return template.path
    if where is not None and where.file:
        return where.file
    raise TemplateError(
        f"{template.template_id} has no fixed path and the finding names no file"
    )


def render(issue: RuleResult) -> Instruction:
    """Build the Section 5.2 instruction for one STATIC issue.

    Raises TemplateError when the rule is not STATIC or has no template, so a
    misrouted issue fails loudly rather than producing a wrong fix.
    """
    rule = issue.rule
    if rule.fix_type is not FixType.STATIC:
        raise TemplateError(
            f"{rule.rule_id} is {rule.fix_type.value}, not STATIC, so it does not "
            f"belong to this module"
        )
    template = get(rule.rule_id)
    where = issue.where[0] if issue.where else None
    return Instruction(
        rule_id=rule.rule_id,
        action=template.action,
        file_path=target(template, where),
        anchor=template.anchor,
        content=template.content,
        rationale=template.rationale,
    )


# --------------------------------------------------------------------------
# the integration point module 3.5 wired
# --------------------------------------------------------------------------

# 3.5 supplies this: send an instruction to the agent over MCP, let 3.6 re-run
# the rule checker, and report whether the rule actually passes now. It returns
# the verified answer, never the agent's own claim.
Apply = Callable[[Instruction], bool]


def resolver(apply: Apply):
    """Build a resolve step for STATIC issues, shaped for the 3.1 controller.

    Returns a callable matching the controller's resolve(issue, attempt) so 3.5
    can register it without changing loop.py. Kept here rather than in loop.py
    because the controller owns sequencing and must not know how any fix type
    is produced.

    A non-STATIC issue is reported as blocked rather than attempted, since
    another module owns it and retrying would waste the budget.
    """
    from prodpilot.loop import Outcome, Step

    def resolve(issue: RuleResult, attempt: int) -> Step:
        try:
            instruction = render(issue)
        except TemplateError as exc:
            return Step(Outcome.BLOCKED, str(exc))
        try:
            passed = apply(instruction)
        except Exception as exc:
            logger.warning("applying %s raised: %s", issue.rule_id, exc)
            return Step(Outcome.UNRESOLVED, f"applying the fix raised: {exc}")
        if passed:
            return Step(Outcome.RESOLVED, f"{instruction.action.value} applied and verified")
        return Step(Outcome.UNRESOLVED, "the rule still fails after the change")

    return resolve


def static_rules() -> tuple[Rule, ...]:
    """Every STATIC rule in the frozen store, for coverage checks."""
    from prodpilot.rules import ALL_RULES

    return tuple(r for r in ALL_RULES if r.fix_type is FixType.STATIC)

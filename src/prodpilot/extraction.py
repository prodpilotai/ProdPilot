"""DYNAMIC-PARAMETRIC extraction routines for the bounded agentic loop.

Scope is Phase 3 module 3.3. This module resolves the 16 rules the frozen store
classifies DYNAMIC-PARAMETRIC, and renders each into the same Section 5.2 first
contract form the STATIC templates use.

It produces no STATIC template (3.2), no DYNAMIC-DELEGATED constraint (3.4), no
MCP wiring (3.5) and no verification (3.6).

What separates this from 3.2
----------------------------
Section 4.1 defines a DYNAMIC-PARAMETRIC rule as one where the fix is a known
template but one or more parameters must be derived from this specific codebase.
The contract shape is identical to STATIC, because the content is still fully
determined by ProdPilot and the agent only applies it. The only difference is
where the parameters come from.

Everything here is mechanical: pattern matching over parsed syntax trees and
over configuration files that have already been read. No model is involved,
ProdPilot's or the agent's, and nothing is generated.

Failing closed
--------------
Section 4.1 requires that an ambiguous extraction be flagged for manual review
rather than guessed. Every routine returns an Extract, and an Extract that is
not ok carries the candidates it found and the reason it refused to choose. The
resolver turns that into a blocked step, so the loop stops spending budget on an
issue no amount of retrying will settle, and the manual review record names the
alternatives a developer has to decide between.

Guessing would be worse than refusing here. Picking the wrong environment
variable name produces a fix that passes its own checker while leaving the
application broken, which is precisely the failure the verify step cannot catch.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from prodpilot import astchecks, filechecks
from prodpilot.audit import RuleResult
from prodpilot.entropy import scan_project
from prodpilot.jsparse import Tree, line_of, name_of, walk
from prodpilot.rules import FixType, get_rule
from prodpilot.templates import CONSTRAINT, LINE, Action, Instruction

logger = logging.getLogger(__name__)

LOCKFILES = {
    "package-lock.json": "npm",
    "yarn.lock": "yarn",
    "pnpm-lock.yaml": "pnpm",
}

# Entry points a Node project uses when package.json does not say.
ENTRY_NAMES = ("server.js", "app.js", "index.js", "main.js")
ENTRY_DIRS = ("", "src", "lib", "app")

# Drivers whose pool configuration differs, so the right one has to be read
# from the manifest rather than assumed.
DRIVERS = {
    "pg": "postgres",
    "mysql2": "mysql",
    "mysql": "mysql",
    "mongoose": "mongoose",
    "mongodb": "mongodb",
}

POOLS = {
    "postgres": 'const pool = new (require("pg").Pool)({\n'
                "  connectionString: process.env.DATABASE_URL,\n"
                "  max: 10,\n  idleTimeoutMillis: 30000,\n});\n",
    "mysql": 'const pool = require("mysql2").createPool({\n'
             "  uri: process.env.DATABASE_URL,\n"
             "  connectionLimit: 10,\n});\n",
    "mongodb": 'const client = new (require("mongodb").MongoClient)(\n'
               "  process.env.DATABASE_URL,\n  { maxPoolSize: 10 },\n);\n",
}

SECRET_WORDS = ("TOKEN", "SECRET", "PASSWORD", "KEY", "CREDENTIAL", "AUTH", "DSN", "URL")


class ExtractError(Exception):
    """Raised when a rule is routed here that this module does not own."""


@dataclass(frozen=True)
class Extract:
    """What a routine derived, or why it refused to choose.

    ok False is not a failure of the routine. It is the routine doing its job,
    per Section 4.1, when the codebase does not settle the question on its own.
    """

    ok: bool
    values: dict[str, str] = field(default_factory=dict)
    candidates: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "values": dict(self.values),
            "candidates": list(self.candidates),
            "reason": self.reason,
        }


def found(**values: str) -> Extract:
    return Extract(True, values=values)


def unclear(reason: str, candidates=()) -> Extract:
    """Refuse to choose, naming what was found so a person can decide."""
    return Extract(False, candidates=tuple(candidates), reason=reason)


@dataclass(frozen=True)
class Source:
    """Everything the routines read, gathered once.

    Composed from the readers the audit engine already has rather than opening
    the same files again.
    """

    root: Path
    project: filechecks.Project
    parsed: astchecks.Sources
    env: str | None
    locks: tuple[str, ...]

    @property
    def pkg(self) -> dict:
        return self.project.pkg or {}

    @property
    def scripts(self) -> dict:
        got = self.pkg.get("scripts")
        return got if isinstance(got, dict) else {}

    @property
    def deps(self) -> set[str]:
        out: set[str] = set()
        for section in ("dependencies", "devDependencies"):
            block = self.pkg.get(section)
            if isinstance(block, dict):
                out.update(block)
        return out

    @property
    def declared(self) -> set[str]:
        """Environment keys the project already declares."""
        return astchecks.declared_keys(self.parsed.env_example) | astchecks.declared_keys(self.env)


def load(root: str | Path) -> Source:
    """Read a project once for every routine."""
    base = Path(root)
    project = filechecks.load(base)
    parsed = astchecks.load_sources(base)
    env = None
    try:
        env = (base / ".env").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        env = None
    locks = tuple(name for name in LOCKFILES if (base / name).is_file())
    return Source(root=base, project=project, parsed=parsed, env=env, locks=locks)


# --------------------------------------------------------------------------
# shared extraction helpers
# --------------------------------------------------------------------------


def screaming(name: str) -> str:
    """Turn an identifier into the SCREAMING_SNAKE form env vars use."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", spaced)
    return cleaned.strip("_").upper()


def prefix_of(declared: set[str]) -> str:
    """A naming prefix the project already uses, when every key shares one.

    Adopted only when it is unanimous across at least two keys. A project split
    across two prefixes has no convention to follow, so none is imposed.
    """
    heads = {k.split("_", 1)[0] + "_" for k in declared if "_" in k}
    if len(heads) == 1 and len(declared) >= 2:
        return heads.pop()
    return ""


def bound_names(tree: Tree, value: str) -> set[str]:
    """Identifiers a literal is bound to in one file.

    Covers a variable declaration, an object property and a plain assignment,
    which is where a credential is written in practice.
    """
    names: set[str] = set()
    for node in walk(tree.ast):
        kind = node.get("type")
        if kind == "VariableDeclarator":
            init = node.get("init") or {}
            if init.get("type") == "Literal" and init.get("value") == value:
                got = (node.get("id") or {}).get("name")
                if got:
                    names.add(got)
        elif kind == "Property":
            val = node.get("value") or {}
            if val.get("type") == "Literal" and val.get("value") == value:
                got = (node.get("key") or {}).get("name") or (node.get("key") or {}).get("value")
                if got:
                    names.add(str(got))
        elif kind == "AssignmentExpression":
            right = node.get("right") or {}
            if right.get("type") == "Literal" and right.get("value") == value:
                got = name_of(node.get("left"))
                if got:
                    names.add(got.split(".")[-1])
    return names


def name_for(src: Source, value: str) -> Extract:
    """Derive the environment variable name a hardcoded value should move to.

    The name comes from the identifier the value is already bound to, put into
    the project's own naming convention. Section 4.1 names this exact routine as
    the DYNAMIC-PARAMETRIC example.

    Refuses when the value is bound to nothing, since there is no name to
    derive, and when it is bound to two different names, since the codebase
    itself does not agree.
    """
    names: set[str] = set()
    for _, tree in src.parsed.ok_trees():
        names |= bound_names(tree, value)

    if not names:
        return unclear(
            "the value is not bound to a named identifier, so no environment "
            "variable name can be derived from it",
            sorted(src.declared) or (),
        )
    if len(names) > 1:
        return unclear(
            f"the value is bound to {len(names)} different names, so the codebase "
            f"does not settle what to call it",
            sorted(screaming(n) for n in names),
        )

    base = screaming(names.pop())
    prefix = prefix_of(src.declared)
    key = base if not prefix or base.startswith(prefix) else prefix + base

    matches = [k for k in src.declared if k == key]
    if not matches:
        near = [k for k in src.declared if base in k or k in base]
        if len(near) > 1:
            return unclear(
                f"{key} is not declared and more than one existing key could be "
                f"the intended one",
                sorted(near),
            )
        if near:
            key = near[0]
    return found(key=key)


def entry_point(src: Source) -> Extract:
    """The file that starts the application.

    Read from package.json main or the start script when either says. Otherwise
    look for a conventional entry file, and refuse when several exist, since
    picking the wrong one produces an image that starts nothing.
    """
    main = src.pkg.get("main")
    if isinstance(main, str) and main.strip():
        return found(entry=main.strip())

    start = src.scripts.get("start")
    if isinstance(start, str):
        hit = re.search(r"([\w./-]+\.(?:js|mjs|cjs))", start)
        if hit:
            return found(entry=hit.group(1))

    seen = []
    for folder in ENTRY_DIRS:
        for name in ENTRY_NAMES:
            rel = f"{folder}/{name}" if folder else name
            if (src.root / rel).is_file():
                seen.append(rel)
    if len(seen) == 1:
        return found(entry=seen[0])
    if not seen:
        return unclear("no entry point is declared and none of the conventional names exist")
    return unclear(
        f"{len(seen)} candidate entry points exist and package.json names none",
        seen,
    )


def manager(src: Source) -> Extract:
    """The package manager, read from the lockfile present."""
    if len(src.locks) == 1:
        return found(manager=LOCKFILES[src.locks[0]], lock=src.locks[0])
    if not src.locks:
        return found(manager="npm", lock="")
    return unclear(
        f"{len(src.locks)} lockfiles are present, so the package manager is unclear",
        sorted(src.locks),
    )


# Each manager's install command, held to its lockfile.
INSTALLS = {"npm": "npm ci", "yarn": "yarn install --frozen-lockfile",
            "pnpm": "pnpm install --frozen-lockfile"}


def install_of(pm: Extract) -> str:
    """The install command for the manager manager found.

    npm ci refuses to run without a package-lock.json, so a project with no
    lockfile at all installs with npm install. Found by module 7.3's full-chain
    run, where every lockfile-less project the loop repaired then failed its
    Docker build. yarn and pnpm are only chosen when their own lockfile exists,
    so their frozen installs always have one to read.
    """
    if not pm.values.get("lock"):
        return "npm install"
    return INSTALLS[pm.values["manager"]]


def node_version(src: Source) -> str:
    """The Node major version to build against."""
    engines = src.pkg.get("engines")
    spec = engines.get("node") if isinstance(engines, dict) else None
    if isinstance(spec, str):
        hit = re.search(r"(\d{2})", spec)
        if hit:
            return hit.group(1)
    return "20"


def base_images(text: str | None) -> list[str]:
    """Base images an existing Dockerfile builds from."""
    if not text:
        return []
    return [
        line.split()[1]
        for line in filechecks.lines_of(text)
        if line.upper().startswith("FROM ") and len(line.split()) > 1
    ]


def run_cmd(text: str | None) -> str | None:
    """The command an existing Dockerfile runs."""
    for line in filechecks.lines_of(text or ""):
        if line.upper().startswith(("CMD ", "ENTRYPOINT ")):
            return line.split(None, 1)[1]
    return None


def driver(src: Source) -> Extract:
    """The database driver in use, read from the manifest."""
    hits = sorted({DRIVERS[d] for d in src.deps if d in DRIVERS})
    if len(hits) == 1:
        return found(driver=hits[0])
    if not hits:
        return unclear("no supported database driver is declared in package.json")
    return unclear(
        f"{len(hits)} database drivers are declared, so the pool to configure is unclear",
        hits,
    )


def mounted(src: Source) -> list[tuple[str, int, str]]:
    """Paths the application mounts routers or routes on, with where each is."""
    out: list[tuple[str, int, str]] = []
    for name, tree in src.parsed.ok_trees():
        if not astchecks.is_app(tree):
            continue
        for call in astchecks.uses(tree) + astchecks.routes(tree):
            args = call.get("arguments") or []
            if args and args[0].get("type") == "Literal":
                target = args[0].get("value")
                if isinstance(target, str) and target.startswith("/"):
                    out.append((name, line_of(args[0]) or 0, target))
    return out


def mounts(src: Source) -> list[str]:
    """Paths the application mounts routers or routes on."""
    return [path for _, _, path in mounted(src)]


def missing_keys(src: Source, prefix: str | None) -> list[str]:
    """Environment keys the code reads that the template does not declare."""
    used: set[str] = set()
    for _, tree in src.parsed.ok_trees():
        for key in astchecks.env_keys(tree):
            if prefix and not key.startswith(prefix):
                continue
            used.add(key)
    return sorted(used - astchecks.declared_keys(src.parsed.env_example))


def host_literals(src: Source) -> list[tuple[str, int, str]]:
    """Backend host literals in source, with where each was found."""
    out: list[tuple[str, int, str]] = []
    for name, tree in src.parsed.ok_trees():
        for node in walk(tree.ast):
            if node.get("type") != "Literal" or not isinstance(node.get("value"), str):
                continue
            text = node["value"]
            if astchecks.HOST_RE.match(text) and len(text) > 12:
                out.append((name, line_of(node) or 0, text))
    return out


def moved(src: Source, file: str, line: int, literal: str,
          make: Callable[[str], str], **values: str) -> Extract:
    """Rewrite the one line holding a literal, with the literal replaced.

    For a fix that swaps a literal for an expression, the contract names the
    line by number and carries the whole rewritten line. Nothing is appended
    anywhere, and a credential being moved out of source never travels inside
    the instruction, since the line it carries no longer holds it. make is given
    the quote the literal used, for a replacement that is itself a string.

    A literal that is not on its line as one quoted string, for example one
    split across lines, refuses rather than guessing which text to change.
    """
    try:
        lines = (src.root / file).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return unclear(f"cannot read {file} to rewrite the line")
    if not 0 < line <= len(lines):
        return unclear(f"{file} has no line {line} to rewrite")
    text = lines[line - 1]
    for quote in ('"', "'", "`"):
        held = quote + literal + quote
        if held in text:
            return found(file=file, line=str(line), anchor=f"{LINE}{line}",
                         replaced=text.replace(held, make(quote), 1), **values)
    return unclear(f"line {line} of {file} does not hold the literal as one string, "
                   f"so which text to change is unclear")


def env_url(key: str, text: str, host: str) -> str:
    """A URL literal with its host read from a Vite environment key."""
    ref = f"import.meta.env.{key}"
    rest = text[len(host):]
    return f"`${{{ref}}}{rest}`" if rest else ref


# --------------------------------------------------------------------------
# routines, one per rule
# --------------------------------------------------------------------------


def dockerfile_node(src: Source, issue: RuleResult) -> Extract:
    """BLD-001: build a Dockerfile from the entry point and pinned runtime."""
    entry = entry_point(src)
    if not entry.ok:
        return entry
    pm = manager(src)
    if not pm.ok:
        return pm
    install = install_of(pm)
    return found(
        entry=entry.values["entry"],
        node=node_version(src),
        install=install,
        manager=pm.values["manager"],
    )


def dockerfile_react(src: Source, issue: RuleResult) -> Extract:
    """BLD-007: build a Dockerfile around the declared build script."""
    build = src.scripts.get("build")
    if not isinstance(build, str) or not build.strip():
        return unclear("package.json declares no build script, so there is nothing to build")
    pm = manager(src)
    if not pm.ok:
        return pm
    install = install_of(pm)
    run = {"npm": "npm run build", "yarn": "yarn build", "pnpm": "pnpm build"}[
        pm.values["manager"]
    ]
    return found(node=node_version(src), install=install, build=run,
                 manager=pm.values["manager"])


def env_template(src: Source, issue: RuleResult) -> Extract:
    """ENV-001 and ENV-003: declare every key the code reads."""
    prefix = "VITE_" if issue.rule.stack.value == "react_vite" else None
    absent = missing_keys(src, prefix)
    if not absent:
        return unclear("every key the code reads is already declared")
    return found(keys="\n".join(f"{k}=" for k in absent), count=str(len(absent)))


def ci_workflow(src: Source, issue: RuleResult) -> Extract:
    """BLD-003 and BLD-010: build a workflow from the manager and scripts."""
    pm = manager(src)
    if not pm.ok:
        return pm
    which = pm.values["manager"]
    install = install_of(pm)
    steps = [install]
    if isinstance(src.scripts.get("build"), str):
        steps.append({"npm": "npm run build", "yarn": "yarn build", "pnpm": "pnpm build"}[which])
    if isinstance(src.scripts.get("test"), str):
        steps.append({"npm": "npm test", "yarn": "yarn test", "pnpm": "pnpm test"}[which])
    return found(node=node_version(src), steps="\n".join(f"      - run: {s}" for s in steps),
                 manager=which)


def start_script(src: Source, issue: RuleResult) -> Extract:
    """BLD-004: name the file the start script should run."""
    return entry_point(src)


def stages_node(src: Source, issue: RuleResult) -> Extract:
    """BLD-006: rebuild the Dockerfile as two stages, keeping its own image."""
    images = base_images(src.project.dockerfile)
    if not images:
        return unclear("the Dockerfile declares no base image to build from")
    if len(set(images)) > 1:
        return unclear(
            f"the Dockerfile already builds from {len(set(images))} different images, "
            f"so which one the runtime stage should use is unclear",
            sorted(set(images)),
        )
    command = run_cmd(src.project.dockerfile)
    if not command:
        return unclear("the Dockerfile declares no CMD, so the runtime stage has no command")
    pm = manager(src)
    if not pm.ok:
        return pm
    install = install_of(pm)
    return found(image=images[0], command=command, install=install)


def stages_react(src: Source, issue: RuleResult) -> Extract:
    """BLD-012: build in one stage and serve the output from a static image."""
    images = base_images(src.project.dockerfile)
    if not images:
        return unclear("the Dockerfile declares no base image to build from")
    build = src.scripts.get("build")
    if not isinstance(build, str) or not build.strip():
        return unclear("package.json declares no build script, so there is nothing to serve")
    pm = manager(src)
    if not pm.ok:
        return pm
    which = pm.values["manager"]
    install = install_of(pm)
    run = {"npm": "npm run build", "yarn": "yarn build", "pnpm": "pnpm build"}[which]
    return found(image=images[0], install=install, build=run)


def secret_name(src: Source, issue: RuleResult) -> Extract:
    """SCR-002 and SCR-004: move a hardcoded credential to an environment key."""
    hits = [h for h in scan_project(src.root) if not h.borderline]
    if not hits:
        return unclear("no credential literal was found to move")
    target = hits[0]
    named = name_for(src, target.value)
    if not named.ok:
        return named
    key = named.values["key"]
    ref = ("import.meta.env." if issue.rule_id == "SCR-004" else "process.env.") + key
    return moved(src, target.file, target.line, target.value, lambda quote: ref,
                 key=key, masked=target.masked)


def db_credentials(src: Source, issue: RuleResult) -> Extract:
    """CON-001: move an inline connection string to an environment key."""
    literals: list[tuple[str, int, str]] = []
    for name, tree in src.parsed.ok_trees():
        for call in astchecks.db_connects(tree):
            for node in walk(call):
                text = node.get("value")
                if (node.get("type") == "Literal" and isinstance(text, str)
                        and "://" in text and "@" in text):
                    literals.append((name, line_of(node) or 0, text))
    if not literals:
        return unclear("no inline connection string was found to move")
    texts = {text for _, _, text in literals}
    if len(texts) > 1:
        return unclear(
            f"{len(texts)} different connection strings are inline, so which "
            f"one the environment key should hold is unclear",
            sorted({t.split("@")[-1] for t in texts}),
        )
    file, line, text = literals[0]
    named = name_for(src, text)
    if named.ok:
        key = named.values["key"]
    else:
        # A connection string is conventionally DATABASE_URL, but only adopt
        # that when the project has not already named one of its own.
        existing = [k for k in src.declared if k.endswith(("_URL", "_URI", "_DSN"))]
        if len(existing) > 1:
            return unclear(
                "more than one existing key could hold the connection string",
                sorted(existing),
            )
        key = existing[0] if existing else (prefix_of(src.declared) or "") + "DATABASE_URL"
    return moved(src, file, line, text, lambda quote: f"process.env.{key}", key=key)


def pool_config(src: Source, issue: RuleResult) -> Extract:
    """CON-002: configure a pool for the driver the project actually uses."""
    which = driver(src)
    if not which.ok:
        return which
    kind = which.values["driver"]
    if kind == "mongoose":
        return unclear("mongoose pools by default, so this rule needs no fix here")
    body = POOLS.get(kind)
    if body is None:
        return unclear(f"no pool configuration is known for the {kind} driver")
    return found(driver=kind, config=body)


def version_prefix(src: Source, issue: RuleResult) -> Extract:
    """API-002: mount routes under a versioned prefix built from the existing ones."""
    places = [m for m in mounted(src) if m[2] not in ("/health", "/healthz", "/metrics")]
    paths = [path for _, _, path in places]
    if not paths:
        return unclear("the application mounts no paths to version")
    versioned = [p for p in paths if re.search(r"/v\d+(/|$)", p)]
    if versioned:
        return unclear("routes are already mounted under a versioned prefix")
    heads = {p.split("/")[1] for p in paths if len(p.split("/")) > 1 and p.split("/")[1]}
    if len(heads) > 1:
        return unclear(
            f"routes are mounted under {len(heads)} different roots, so one versioned "
            f"prefix cannot be derived",
            sorted("/" + h for h in heads),
        )
    head = heads.pop() if heads else "api"
    base = "/api/v1" if head == "api" else f"/{head}/v1"
    # The first mount moves under the prefix, keeping whatever followed its
    # root, so /orders/:id becomes /orders/v1/:id rather than losing its path.
    file, line, current = places[0]
    stem = "/" + head
    if current == stem or current.startswith(stem + "/"):
        new = base + current[len(stem):]
    else:
        new = base if current == "/" else base + current
    return moved(src, file, line, current, lambda quote: quote + new + quote,
                 prefix=base, current=current)


def api_url(src: Source, issue: RuleResult) -> Extract:
    """ENV-004: move a request target to import.meta.env."""
    targets: list[tuple[str, int, str]] = []
    for name, tree in src.parsed.ok_trees():
        for node in walk(tree.ast):
            if node.get("type") != "CallExpression":
                continue
            callee = name_of(node.get("callee")) or ""
            if callee != "fetch" and not callee.startswith("axios"):
                continue
            for inner in walk(node):
                text = inner.get("value")
                if (inner.get("type") == "Literal" and isinstance(text, str)
                        and astchecks.HOST_RE.match(text)):
                    targets.append((name, line_of(inner) or 0, text))
    if not targets:
        return unclear("no request targets a literal host")
    roots = {re.match(r"https?://[^/]+", t).group(0) for _, _, t in targets}
    if len(roots) > 1:
        return unclear(
            f"requests target {len(roots)} different hosts, so one base URL cannot "
            f"be derived",
            sorted(roots),
        )
    host = roots.pop()
    key = "VITE_API_BASE_URL"
    file, line, text = targets[0]
    return moved(src, file, line, text, lambda quote: env_url(key, text, host),
                 key=key, host=host)


def backend_url(src: Source, issue: RuleResult) -> Extract:
    """ENV-005: move a backend host literal to an environment key."""
    hits = host_literals(src)
    if not hits:
        return unclear("no backend host literal was found")
    roots = {re.match(r"https?://[^/]+", t).group(0) for _, _, t in hits}
    if len(roots) > 1:
        return unclear(
            f"{len(roots)} different hosts appear in source, so one key cannot hold "
            f"them all",
            sorted(roots),
        )
    name, line, text = hits[0]
    host = roots.pop()
    key = "VITE_API_BASE_URL"
    return moved(src, name, line, text, lambda quote: env_url(key, text, host),
                 key=key, host=host)


ROUTINES: dict[str, Callable[[Source, RuleResult], Extract]] = {
    "BLD-001": dockerfile_node,
    "ENV-001": env_template,
    "BLD-003": ci_workflow,
    "BLD-004": start_script,
    "BLD-006": stages_node,
    "SCR-002": secret_name,
    "CON-001": db_credentials,
    "CON-002": pool_config,
    "API-002": version_prefix,
    "BLD-007": dockerfile_react,
    "ENV-003": env_template,
    "BLD-010": ci_workflow,
    "BLD-012": stages_react,
    "ENV-004": api_url,
    "ENV-005": backend_url,
    "SCR-004": secret_name,
}


# --------------------------------------------------------------------------
# rendering the Section 5.2 contract
# --------------------------------------------------------------------------

DOCKERFILE_NODE = """\
FROM node:{node}-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN {install}
COPY . .

FROM node:{node}-alpine AS runtime
WORKDIR /app
COPY --from=build /app ./
USER node
CMD ["node", "{entry}"]
"""

DOCKERFILE_REACT = """\
FROM node:{node}-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN {install}
COPY . .
RUN {build}

FROM nginx:1.27-alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
RUN chown -R nginx:nginx /var/cache/nginx /var/log/nginx /etc/nginx/conf.d && touch /var/run/nginx.pid && chown nginx:nginx /var/run/nginx.pid
USER nginx
EXPOSE 8080
"""

WORKFLOW = """\
name: deploy
on:
  push:
    branches: [main]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "{node}"
{steps}
"""

STAGES_NODE = """\
FROM {image} AS build
WORKDIR /app
COPY package*.json ./
RUN {install}
COPY . .

FROM {image} AS runtime
WORKDIR /app
COPY --from=build /app ./
USER node
CMD {command}
"""

STAGES_REACT = """\
FROM {image} AS build
WORKDIR /app
COPY package*.json ./
RUN {install}
COPY . .
RUN {build}

FROM nginx:1.27-alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
RUN chown -R nginx:nginx /var/cache/nginx /var/log/nginx /etc/nginx/conf.d && touch /var/run/nginx.pid && chown nginx:nginx /var/run/nginx.pid
USER nginx
EXPOSE 8080
"""


@dataclass(frozen=True)
class Shape:
    """How a rule's extracted values become an instruction."""

    action: Action
    anchor: str
    body: str
    rationale: str
    path: str | None = None


SHAPES: dict[str, Shape] = {
    "BLD-001": Shape(Action.CREATE_FILE, "", DOCKERFILE_NODE,
                     "Builds the image from the project's own entry point and pinned runtime.",
                     "Dockerfile"),
    "BLD-007": Shape(Action.CREATE_FILE, "", DOCKERFILE_REACT,
                     "Builds the bundle and serves it from a static image.", "Dockerfile"),
    "BLD-003": Shape(Action.CREATE_FILE, "", WORKFLOW,
                     "Runs the project's own install and build steps on every push.",
                     ".github/workflows/deploy.yml"),
    "BLD-010": Shape(Action.CREATE_FILE, "", WORKFLOW,
                     "Runs the project's own install and build steps on every push.",
                     ".github/workflows/deploy.yml"),
    # The two stage bodies are whole Dockerfiles, so they replace the file
    # rather than being appended to the single stage one already there.
    "BLD-006": Shape(Action.CREATE_FILE, "", STAGES_NODE,
                     "Splits the build so tooling stays out of the runtime image.",
                     "Dockerfile"),
    "BLD-012": Shape(Action.CREATE_FILE, "", STAGES_REACT,
                     "Builds in one stage and serves the output from a static image.",
                     "Dockerfile"),
    "BLD-004": Shape(Action.INSERT_AFTER, "package:scripts", '"start": "node {entry}",\n',
                     "Runs the entry point directly rather than through a development watcher.",
                     "package.json"),
    "ENV-001": Shape(Action.INSERT_AFTER, "file:end", "{keys}\n",
                     "Declares every environment key the code reads.", ".env.example"),
    "ENV-003": Shape(Action.INSERT_AFTER, "file:end", "{keys}\n",
                     "Declares every VITE_ key the code reads.", ".env.example"),
    # The literal rewrites name the line through moved, which supplies both
    # the anchor and the rewritten line.
    "SCR-002": Shape(Action.REPLACE_BLOCK, "{anchor}", "{replaced}\n",
                     "Moves the credential out of source and reads it from the environment."),
    "SCR-004": Shape(Action.REPLACE_BLOCK, "{anchor}", "{replaced}\n",
                     "Moves the credential out of source and reads it from the environment."),
    "CON-001": Shape(Action.REPLACE_BLOCK, "{anchor}", "{replaced}\n",
                     "Reads the connection string from the environment."),
    "CON-002": Shape(Action.INSERT_AFTER, "file:end", "{config}",
                     "Opens one pooled connection for the driver the project uses."),
    "API-002": Shape(Action.REPLACE_BLOCK, "{anchor}", "{replaced}\n",
                     "Mounts routes under a versioned prefix so the API can change safely."),
    "ENV-004": Shape(Action.REPLACE_BLOCK, "{anchor}", "{replaced}\n",
                     "Reads the API base URL from the environment instead of a literal."),
    "ENV-005": Shape(Action.REPLACE_BLOCK, "{anchor}", "{replaced}\n",
                     "Reads the backend host from the environment instead of a literal."),
}


# The extraction_spec each routine implements, as the frozen store records it.
# Kept explicit so a test can hold this module and rules.py to each other.
SPECS: dict[str, str] = {
    "BLD-001": "ext.node.dockerfile",
    "ENV-001": "ext.node.env_keys",
    "BLD-003": "ext.node.ci_workflow",
    "BLD-004": "ext.node.start_script",
    "BLD-006": "ext.node.multistage_build",
    "SCR-002": "ext.node.secret_to_env_var",
    "CON-001": "ext.node.db_credentials",
    "CON-002": "ext.node.connection_pool",
    "API-002": "ext.node.api_version_prefix",
    "BLD-007": "ext.react.dockerfile",
    "ENV-003": "ext.react.env_keys",
    "BLD-010": "ext.react.ci_workflow",
    "BLD-012": "ext.react.multistage_build",
    "ENV-004": "ext.react.api_url_env_var",
    "ENV-005": "ext.react.backend_url_env_var",
    "SCR-004": "ext.react.secret_to_env_var",
}


def covers(rule_id: str) -> bool:
    """Whether this module owns a rule."""
    return rule_id in ROUTINES


def extract(src: Source, issue: RuleResult) -> Extract:
    """Run the routine for one issue.

    Raises when the rule is not DYNAMIC-PARAMETRIC or has no routine, so a
    misrouted issue fails loudly rather than producing a wrong fix.
    """
    rule = issue.rule
    if rule.fix_type is not FixType.DYNAMIC_PARAMETRIC:
        raise ExtractError(
            f"{rule.rule_id} is {rule.fix_type.value}, not DYNAMIC-PARAMETRIC"
        )
    routine = ROUTINES.get(rule.rule_id)
    if routine is None:
        raise ExtractError(f"no extraction routine for {rule.rule_id}")
    return routine(src, issue)


def render(src: Source, issue: RuleResult) -> Instruction:
    """Build the Section 5.2 instruction once extraction has succeeded.

    Raises when extraction refused, since the caller must handle that as manual
    review rather than turn it into a fix.
    """
    got = extract(src, issue)
    if not got.ok:
        raise ExtractError(f"{issue.rule_id}: {got.reason}")

    shape = SHAPES[issue.rule_id]
    where = issue.where[0] if issue.where else None
    path = shape.path or got.values.get("file") or (where.file if where else None)
    if not path:
        raise ExtractError(f"{issue.rule_id}: nothing names the file to change")

    action, anchor = shape.action, shape.anchor.format(**got.values)
    if action is Action.INSERT_AFTER and anchor == "file:end" and not (src.root / path).is_file():
        # The end of a file that does not exist yet is the start of a new one,
        # for example an .env.example the project never had.
        action, anchor = Action.CREATE_FILE, ""

    return Instruction(
        rule_id=issue.rule_id,
        action=action,
        file_path=path,
        anchor=anchor,
        content=shape.body.format(**got.values),
        rationale=shape.rationale,
        constraint=CONSTRAINT,
    )


# --------------------------------------------------------------------------
# the integration point module 3.5 wired
# --------------------------------------------------------------------------

Apply = Callable[[Instruction], bool]


def resolver(apply: Apply, root: str | Path):
    """Build a resolve step for DYNAMIC-PARAMETRIC issues, shaped for 3.1.

    Follows the pattern 3.2 established. It takes the project root as well as
    the apply step, because unlike a STATIC template these routines have to read
    the codebase.

    An ambiguous extraction is reported blocked, not unresolved. Retrying would
    re-read the same unchanged codebase and refuse again, so spending the rest
    of the budget on it would only delay the manual review the developer needs.

    The project is read again on every attempt rather than once. These routines
    derive their parameters from the codebase, and the codebase changes as
    earlier fixes land, so a cached read would give a later rule a stale view.
    BLD-006 restructuring a Dockerfile that BLD-001 has just created is the
    obvious case.
    """
    from prodpilot.loop import Outcome, Step

    def resolve(issue: RuleResult, attempt: int) -> Step:
        try:
            src = load(root)
        except (OSError, NotADirectoryError) as exc:
            return Step(Outcome.BLOCKED, f"cannot read the project: {exc}")
        try:
            got = extract(src, issue)
        except ExtractError as exc:
            return Step(Outcome.BLOCKED, str(exc))
        if not got.ok:
            detail = got.reason
            if got.candidates:
                detail += ". Candidates: " + ", ".join(got.candidates)
            return Step(Outcome.BLOCKED, detail)
        try:
            instruction = render(src, issue)
        except ExtractError as exc:
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


def parametric_rules():
    """Every DYNAMIC-PARAMETRIC rule in the frozen store, for coverage checks."""
    from prodpilot.rules import ALL_RULES

    return tuple(r for r in ALL_RULES if r.fix_type is FixType.DYNAMIC_PARAMETRIC)

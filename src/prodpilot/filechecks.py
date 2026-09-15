"""File existence and content checks for the audit engine.

Scope is Phase 2 module 2.3, covering every rule in the frozen store whose
check_type is file_existence or entropy_scan. Nothing is fixed and nothing is
scored. Fix generation is Phase 3 and score computation is module 2.4.

Every check maps to a rule that already exists from module 2.1, by rule_id. No
rule is defined here.

Half of these rules read file content rather than only testing for presence,
which the CheckType docstring in rules.py already sets out: the boundary is the
parser required, not the question asked. Anything answered by reading lines from
a known configuration file belongs here, whether it asks "is this file present"
or "does this file say this".

Finding and Status come from the shared findings module, so module 2.4
receives one shape from the whole audit engine.
"""

from __future__ import annotations

import re
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from prodpilot.findings import Finding, Status
from prodpilot.blueprint import Stack
from prodpilot.detection import detect_stack
from prodpilot.entropy import HistoryUnavailable, is_repo, scan_history, scan_project

logger = logging.getLogger(__name__)

WORKFLOW_DIR = ".github/workflows"
NGINX_NAMES = ("nginx.conf", "default.conf", "nginx.default.conf", "site.conf")

# Header names that satisfy the security header requirement for a static site.
SECURITY_HEADERS = (
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
)


@dataclass(frozen=True)
class Project:
    """A project under audit, with its config files read once.

    Every check needs some subset of the same handful of files, so they are
    read here rather than reopened per rule.
    """

    root: Path
    gitignore: str | None
    dockerfile: str | None
    dockerignore: bool
    pkg: dict | None
    pkg_broken: bool
    nginx: str | None
    workflows: list[str]

    @property
    def has_pkg(self) -> bool:
        return self.pkg is not None


def read(path: Path) -> str | None:
    """File contents, or None when it does not exist or cannot be read."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def find_nginx(root: Path) -> str | None:
    """Contents of an nginx configuration, wherever it sits in the project."""
    for name in NGINX_NAMES:
        text = read(root / name)
        if text is not None:
            return text
    for path in root.rglob("*.conf"):
        if "node_modules" in path.parts or ".git" in path.parts:
            continue
        text = read(path)
        if text and ("server" in text and "listen" in text):
            return text
    return None


def load(root: str | Path) -> Project:
    """Read every config file the checks need."""
    base = Path(root)
    if not base.is_dir():
        raise NotADirectoryError(f"not a project directory: {base}")

    raw_pkg = read(base / "package.json")
    pkg: dict | None = None
    broken = False
    if raw_pkg is not None:
        try:
            parsed = json.loads(raw_pkg)
            pkg = parsed if isinstance(parsed, dict) else None
            broken = not isinstance(parsed, dict)
        except json.JSONDecodeError:
            broken = True

    flows: list[str] = []
    wf_dir = base / ".github" / "workflows"
    if wf_dir.is_dir():
        for path in sorted(wf_dir.iterdir()):
            if path.suffix in (".yml", ".yaml") and path.is_file():
                flows.append(path.name)

    return Project(
        root=base,
        gitignore=read(base / ".gitignore"),
        dockerfile=read(base / "Dockerfile"),
        dockerignore=(base / ".dockerignore").is_file(),
        pkg=pkg,
        pkg_broken=broken,
        nginx=find_nginx(base),
        workflows=flows,
    )


def ignored(text: str | None, *names: str) -> bool:
    """Whether a .gitignore excludes any of the given names."""
    if not text:
        return False
    for raw in text.splitlines():
        entry = raw.strip().rstrip("/")
        if not entry or entry.startswith("#"):
            continue
        for name in names:
            target = name.rstrip("/")
            if entry == target or entry == f"/{target}":
                return True
            # A pattern such as .env* or *.local covers the target too.
            if entry.endswith("*") and target.startswith(entry[:-1]):
                return True
    return False


def lines_of(text: str | None) -> list[str]:
    """Non-comment, non-blank lines of a config file."""
    if not text:
        return []
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


# --------------------------------------------------------------------------
# presence checks
# --------------------------------------------------------------------------


def has_dockerfile(p: Project, rule: str) -> Finding:
    if p.dockerfile is None:
        return Finding(rule, Status.FAIL, "Dockerfile", None,
                       "no Dockerfile at the project root, so the project cannot be containerised")
    return Finding(rule, Status.PASS, "Dockerfile", None, "Dockerfile is present")


def has_dockerignore(p: Project, rule: str) -> Finding:
    if not p.dockerignore:
        return Finding(rule, Status.FAIL, ".dockerignore", None,
                       "no .dockerignore, so node_modules and .env files would be copied "
                       "into the image")
    return Finding(rule, Status.PASS, ".dockerignore", None, ".dockerignore is present")


def has_gitignore(p: Project, rule: str) -> Finding:
    if p.gitignore is None:
        return Finding(rule, Status.FAIL, ".gitignore", None, "no .gitignore at the project root")
    return Finding(rule, Status.PASS, ".gitignore", None, ".gitignore is present")


def has_workflow(p: Project, rule: str) -> Finding:
    if not p.workflows:
        return Finding(rule, Status.FAIL, WORKFLOW_DIR, None,
                       "no GitHub Actions workflow, so every deployment stays manual")
    return Finding(rule, Status.PASS, f"{WORKFLOW_DIR}/{p.workflows[0]}", None,
                   f"{len(p.workflows)} workflow file(s) present")


def has_nginx(p: Project, rule: str) -> Finding:
    if p.nginx is None:
        return Finding(rule, Status.FAIL, "nginx.conf", None,
                       "no nginx configuration, so the built assets have nothing to serve them")
    return Finding(rule, Status.PASS, "nginx.conf", None, "an nginx configuration is present")


# --------------------------------------------------------------------------
# gitignore content
# --------------------------------------------------------------------------


def ignores_modules(p: Project, rule: str) -> Finding:
    if p.gitignore is None:
        return Finding(rule, Status.FAIL, ".gitignore", None, "no .gitignore to check")
    if ignored(p.gitignore, "node_modules"):
        return Finding(rule, Status.PASS, ".gitignore", None, "node_modules is excluded")
    return Finding(rule, Status.FAIL, ".gitignore", None, "node_modules is not excluded")


def ignores_dist(p: Project, rule: str) -> Finding:
    if p.gitignore is None:
        return Finding(rule, Status.FAIL, ".gitignore", None, "no .gitignore to check")
    if ignored(p.gitignore, "dist", "build"):
        return Finding(rule, Status.PASS, ".gitignore", None, "the build output is excluded")
    return Finding(rule, Status.FAIL, ".gitignore", None, "the dist build output is not excluded")


def ignores_env(p: Project, rule: str) -> Finding:
    if p.gitignore is None:
        return Finding(rule, Status.FAIL, ".gitignore", None,
                       "no .gitignore, so a committed .env would not be caught")
    has_env = ignored(p.gitignore, ".env")
    has_prod = ignored(p.gitignore, ".env.production")
    if has_env and has_prod:
        return Finding(rule, Status.PASS, ".gitignore", None,
                       ".env and .env.production are both excluded")
    missing = [n for n, ok in ((".env", has_env), (".env.production", has_prod)) if not ok]
    return Finding(rule, Status.FAIL, ".gitignore", None,
                   f"not excluded: {', '.join(missing)}")


# --------------------------------------------------------------------------
# package.json content
# --------------------------------------------------------------------------


def pkg_guard(p: Project, rule: str) -> Finding | None:
    """Shared precondition for every package.json check."""
    if p.pkg_broken:
        return Finding(rule, Status.UNPARSED, "package.json", None,
                       "package.json is present but is not a JSON object")
    if not p.has_pkg:
        return Finding(rule, Status.FAIL, "package.json", None, "no package.json at the project root")
    return None


def has_start(p: Project, rule: str) -> Finding:
    blocked = pkg_guard(p, rule)
    if blocked:
        return blocked
    scripts = p.pkg.get("scripts")
    if not isinstance(scripts, dict) or not scripts.get("start"):
        return Finding(rule, Status.FAIL, "package.json", None,
                       "no start script, so the platform has no command to run")
    start = str(scripts["start"])
    watchers = ("nodemon", "ts-node-dev", "--watch", "concurrently")
    hit = next((w for w in watchers if w in start), None)
    if hit:
        return Finding(rule, Status.FAIL, "package.json", None,
                       f"the start script runs {hit}, which is a development watcher")
    return Finding(rule, Status.PASS, "package.json", None, "start script runs the server directly")


def has_build(p: Project, rule: str) -> Finding:
    blocked = pkg_guard(p, rule)
    if blocked:
        return blocked
    scripts = p.pkg.get("scripts")
    if not isinstance(scripts, dict) or not scripts.get("build"):
        return Finding(rule, Status.FAIL, "package.json", None,
                       "no build script, so there is no production bundle to serve")
    return Finding(rule, Status.PASS, "package.json", None, "a build script is declared")


def has_engine(p: Project, rule: str) -> Finding:
    blocked = pkg_guard(p, rule)
    if blocked:
        return blocked
    engines = p.pkg.get("engines")
    node = engines.get("node") if isinstance(engines, dict) else None
    if not node:
        return Finding(rule, Status.FAIL, "package.json", None,
                       "engines.node is not set, so the platform picks the runtime version")
    if "20" not in str(node):
        return Finding(rule, Status.FAIL, "package.json", None,
                       f"engines.node is {node}, which does not pin Node 20 LTS")
    return Finding(rule, Status.PASS, "package.json", None, f"engines.node is {node}")


# --------------------------------------------------------------------------
# Dockerfile content
# --------------------------------------------------------------------------


def docker_guard(p: Project, rule: str) -> Finding | None:
    if p.dockerfile is None:
        return Finding(rule, Status.FAIL, "Dockerfile", None, "no Dockerfile to check")
    return None


def has_multistage(p: Project, rule: str) -> Finding:
    blocked = docker_guard(p, rule)
    if blocked:
        return blocked
    stages = [ln for ln in lines_of(p.dockerfile) if ln.upper().startswith("FROM ")]
    if len(stages) < 2:
        return Finding(rule, Status.FAIL, "Dockerfile", None,
                       f"only {len(stages)} build stage, so build tooling ships in the "
                       f"runtime image")
    return Finding(rule, Status.PASS, "Dockerfile", None, f"{len(stages)} build stages")


def has_nonroot(p: Project, rule: str) -> Finding:
    blocked = docker_guard(p, rule)
    if blocked:
        return blocked
    for line in lines_of(p.dockerfile):
        if line.upper().startswith("USER "):
            who = line.split(None, 1)[1].strip()
            if who.lower() in ("root", "0"):
                return Finding(rule, Status.FAIL, "Dockerfile", None,
                               "the image explicitly runs as root")
            return Finding(rule, Status.PASS, "Dockerfile", None, f"runs as {who}")
    return Finding(rule, Status.FAIL, "Dockerfile", None,
                   "no USER instruction, so the process runs as root")


# --------------------------------------------------------------------------
# nginx content
# --------------------------------------------------------------------------


def nginx_guard(p: Project, rule: str) -> Finding | None:
    if p.nginx is None:
        return Finding(rule, Status.FAIL, "nginx.conf", None, "no nginx configuration to check")
    return None


def has_spa_fallback(p: Project, rule: str) -> Finding:
    blocked = nginx_guard(p, rule)
    if blocked:
        return blocked
    body = p.nginx.lower()
    if "try_files" in body and "index.html" in body:
        return Finding(rule, Status.PASS, "nginx.conf", None,
                       "unmatched routes fall back to index.html")
    return Finding(rule, Status.FAIL, "nginx.conf", None,
                   "no try_files fallback to index.html, so a client route 404s on refresh")


def has_health_path(p: Project, rule: str) -> Finding:
    blocked = nginx_guard(p, rule)
    if blocked:
        return blocked
    body = p.nginx.lower()
    if "/health" in body or "/healthz" in body:
        return Finding(rule, Status.PASS, "nginx.conf", None, "a health path is served")
    return Finding(rule, Status.FAIL, "nginx.conf", None,
                   "no health path, so the platform cannot probe the service cheaply")


# A location block's own logging does not describe the server's. ProdPilot's own
# nginx template turns access logging off for /health alone, which module 7.3's
# full-chain run found failing OBS-006 as though logging were off everywhere.
LOCATION = re.compile(r"location\b[^{]*\{[^{}]*\}")


def has_logging(p: Project, rule: str) -> Finding:
    blocked = nginx_guard(p, rule)
    if blocked:
        return blocked
    body = p.nginx.lower()
    if "access_log" in body and "error_log" in body:
        if "access_log off" in LOCATION.sub("", body):
            return Finding(rule, Status.FAIL, "nginx.conf", None, "access logging is turned off")
        return Finding(rule, Status.PASS, "nginx.conf", None, "access and error logging are set")
    missing = [n for n in ("access_log", "error_log") if n not in body]
    return Finding(rule, Status.FAIL, "nginx.conf", None, f"not configured: {', '.join(missing)}")


def has_headers(p: Project, rule: str) -> Finding:
    blocked = nginx_guard(p, rule)
    if blocked:
        return blocked
    body = p.nginx.lower()
    found = [h for h in SECURITY_HEADERS if h in body]
    if "content-security-policy" in found:
        return Finding(rule, Status.PASS, "nginx.conf", None,
                       f"{len(found)} security header(s) set including CSP")
    return Finding(rule, Status.FAIL, "nginx.conf", None,
                   "no Content Security Policy header is set by the serving layer")


# --------------------------------------------------------------------------
# entropy rules
# --------------------------------------------------------------------------


def no_secrets(p: Project, rule: str) -> Finding:
    """Source must carry no credential literal."""
    hits = scan_project(p.root)
    if not hits:
        return Finding(rule, Status.PASS, str(p.root.name), None,
                       "no credential-shaped strings in source")
    first = hits[0]
    sure = [h for h in hits if not h.borderline]
    edge = len(hits) - len(sure)
    detail = f"{len(hits)} credential-shaped string(s), first {first.masked} in {first.file}"
    if edge:
        detail += f". {edge} borderline and reported rather than dismissed"
    return Finding(rule, Status.FAIL, first.file, first.line, detail)


def clean_history(p: Project, rule: str) -> Finding:
    """Committed history must carry no credential, even one since deleted."""
    if not is_repo(p.root):
        return Finding(rule, Status.SKIPPED, str(p.root.name), None,
                       "not a Git repository, so there is no history to scan")
    try:
        hits = scan_history(p.root)
    except HistoryUnavailable as exc:
        return Finding(rule, Status.UNPARSED, str(p.root.name), None, str(exc))
    if not hits:
        return Finding(rule, Status.PASS, str(p.root.name), None,
                       "no credential-shaped strings in committed history")
    first = hits[0]
    return Finding(rule, Status.FAIL, first.file, None,
                   f"{len(hits)} credential-shaped string(s) in history, first {first.masked} "
                   f"in {first.file}")


# --------------------------------------------------------------------------
# rule wiring
# --------------------------------------------------------------------------

Check = Callable[[Project, str], Finding]

NODE_CHECKS: dict[str, Check] = {
    "BLD-001": has_dockerfile,
    "BLD-002": has_dockerignore,
    "GIT-001": has_gitignore,
    "BLD-003": has_workflow,
    "GIT-002": ignores_modules,
    "SCR-001": ignores_env,
    "BLD-004": has_start,
    "BLD-005": has_engine,
    "BLD-006": has_multistage,
    "SEC-001": has_nonroot,
    "SCR-002": no_secrets,
    "GIT-003": clean_history,
}

REACT_CHECKS: dict[str, Check] = {
    "BLD-007": has_dockerfile,
    "BLD-008": has_dockerignore,
    "BLD-009": has_nginx,
    "GIT-004": has_gitignore,
    "BLD-010": has_workflow,
    "GIT-005": ignores_modules,
    "GIT-006": ignores_dist,
    "SCR-003": ignores_env,
    "BLD-011": has_build,
    "BLD-012": has_multistage,
    "BLD-013": has_spa_fallback,
    "SEC-005": has_nonroot,
    "OBS-005": has_health_path,
    "OBS-006": has_logging,
    "SEC-006": has_headers,
    "SCR-004": no_secrets,
    "GIT-007": clean_history,
}

CHECKS_BY_STACK: dict[Stack, dict[str, Check]] = {
    Stack.NODE_EXPRESS: NODE_CHECKS,
    Stack.REACT_VITE: REACT_CHECKS,
}


def check_project(root: str | Path, stack: Stack | None = None) -> list[Finding]:
    """Run every file and entropy check for a project's stack.

    The stack decides which rules apply, since the two blueprints differ. When
    it is not supplied it is detected. An unrecognised project has no blueprint
    and therefore nothing to check against, so the result is empty.
    """
    project = load(root)
    if stack is None:
        stack = detect_stack(project.root).stack
    table = CHECKS_BY_STACK.get(stack)
    if not table:
        logger.info("no file checks for stack %s at %s", stack.value, project.root)
        return []
    out = [fn(project, rule_id) for rule_id, fn in table.items()]
    logger.info("ran %s file and entropy checks on %s", len(out), project.root)
    return out

"""Whether a project builds, the way Render builds it.

Scope is the one feature Phase 5 was missing. Module 5.3 labelled 684 projects
by whether they deployed on Render and answered their health path, and module
5.4 learned from the audit's rule counts which of them would. For React Vite it
could not: projects that deployed and projects that did not looked the same to
it once their failing rules were cleared, because what mostly decides whether a
static site deploys is whether its build succeeds, and no audit rule measures
that. This module measures it.

What it runs
------------
Render's own build command, exactly as module 5.3 gave it to Render through
labels.serves: npm install and then npm run build for React Vite, npm install
for Node Express. It runs in a Linux container, because Render builds on Linux,
from a copy of the project made inside the container, so the project on disk is
mounted read only and never written.

Which Node version
------------------
The one Render would choose, in Render's documented order: .node-version, then
.nvmrc, then the engines field in package.json, then Render's default for new
services, Node 24. A range resolves the way Render resolves it, so an unbounded
range such as >=14 means the newest release. The major is then run in a local
image that carries it. A project asking for a major no local image carries is
built on Node 24 and marked as a fallback on its result, rather than silently
run on something else.

What it will not call a failure
-------------------------------
Only the project's own build failing is a failure. A build that failed on what
looks like the network is retried once, and a second such failure, like a build
that runs past the time limit, is undetermined: it tells nothing about the
project, so it is never recorded as a zero, the same rule module 5.3 applied to
deployments Render never ran. Docker itself refusing to start the container is
not a result at all and raises, because it is a fault here, not in the project.

Why it is not module 6.3
------------------------
Module 6.3 builds a project's Dockerfile. Render deployed every labelled project
natively rather than from a Dockerfile, and 588 of the 684 had no Dockerfile at
all, so a Dockerfile build measures something Render never did. Of the 96 that
had one, 14 Express projects failed the Docker build and still deployed.

When it runs
------------
Before deployment, so it is something the gate can know. It was run once over
the labelled dataset, a week after the deployments it is set beside, and it runs
again for every project the gate is asked about.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from prodpilot import labels

logger = logging.getLogger(__name__)

DATA = Path("data")
BUILDS = DATA / "builds.jsonl"

# Local images, newest first within each major, so each major resolves to one.
IMAGES = ("node:lts", "node:latest", "node:22-slim", "node:20", "node:16", "node:12", "node:10")

# Render's default for services created from 2026-04-21, which every labelling
# service was.
DEFAULT = 24

# Seconds. A build still running after this is undetermined, not failed.
TIMEOUT = 1200

NETWORK = re.compile(
    r"ETIMEDOUT|ECONNRESET|EAI_AGAIN|ENOTFOUND|ECONNREFUSED|socket hang up"
    r"|network (?:request|timeout|error)|registry\.npmjs\.org.*(?:5\d\d|timed out)",
    re.IGNORECASE)

CODENAMES = {"argon": 4, "boron": 6, "carbon": 8, "dubnium": 10, "erbium": 12,
             "fermium": 14, "gallium": 16, "hydrogen": 18, "iron": 20, "jod": 22,
             "krypton": 24}

BUILT = "built"
FAILED = "failed"
UNDETERMINED = "undetermined"

# Docker's own exit codes for a container it could not create or start.
REFUSED = (125, 126, 127)


class BuildError(Exception):
    """Raised when the check itself cannot run, as opposed to the build failing."""


@dataclass(frozen=True)
class Build:
    """What one build attempt found."""

    outcome: str
    image: str = ""
    wanted: int = DEFAULT
    requested: str = ""
    source: str = "default"
    fallback: bool = False
    exit: int | None = None
    seconds: float = 0.0
    attempts: int = 0
    reason: str = ""
    tail: str = ""

    @property
    def built(self) -> int | None:
        """1 if it built, 0 if it failed, None when nothing was learned."""
        if self.outcome == BUILT:
            return 1
        if self.outcome == FAILED:
            return 0
        return None

    def to_dict(self) -> dict[str, object]:
        return {"outcome": self.outcome, "built": self.built, "image": self.image,
                "wanted": self.wanted, "requested": self.requested,
                "source": self.source, "fallback": self.fallback, "exit": self.exit,
                "seconds": round(self.seconds, 1), "attempts": self.attempts,
                "reason": self.reason, "tail": self.tail}


# Runs one docker command with a time limit and answers with its exit code, or
# None when it ran out of time, and its combined output.
Run = Callable[[list[str], float], tuple[int | None, str]]


def docker(args: list[str], limit: float) -> tuple[int | None, str]:
    """The default runner. Kills a container that runs out of time."""
    try:
        done = subprocess.run(["docker", *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=limit)
    except FileNotFoundError as exc:
        raise BuildError("docker is not installed or is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        if "--name" in args:
            subprocess.run(["docker", "kill", args[args.index("--name") + 1]],
                           capture_output=True)
        out = exc.stdout or ""
        return None, out.decode("utf-8", "replace") if isinstance(out, bytes) else out
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def command(stack: str) -> str:
    """Render's build command for a stack, from module 5.3's own mapping."""
    build, _, _, _ = labels.serves(stack)
    return build


_images: dict[int, str] | None = None


def images(run: Run = docker) -> dict[int, str]:
    """Which local image carries which Node major, read from the images."""
    global _images
    if _images is None:
        found: dict[int, str] = {}
        for image in IMAGES:
            code, out = run(["run", "--rm", "--network", "none", image, "node", "--version"], 120)
            out = (out or "").strip()
            if code == 0 and out.startswith("v"):
                found.setdefault(int(out[1:].split(".")[0]), image)
        if not found:
            raise BuildError("no local Node image could be run")
        _images = found
    return _images


def requested(root: Path) -> tuple[str, str]:
    """The Node version a project asks for, and where it asked, in Render's order."""
    for name in (".node-version", ".nvmrc"):
        if (root / name).is_file():
            return (root / name).read_text(encoding="utf-8", errors="replace").strip(), name
    try:
        pkg = json.loads((root / "package.json").read_text(encoding="utf-8", errors="replace"))
        ask = (pkg.get("engines") or {}).get("node") if isinstance(pkg, dict) else None
        if isinstance(ask, str) and ask.strip():
            return ask.strip(), "engines"
    except (OSError, ValueError):
        pass
    return "", "default"


def major(ask: str, known: list[int]) -> int:
    """The Node major a requested version resolves to.

    An exact or bounded request names its major. An unbounded range resolves to
    the newest release, as Render's documentation warns.
    """
    text = ask.lower().strip().lstrip("v")
    if not text:
        return DEFAULT
    if text in ("node", "latest", "current", "*", "x"):
        return max(known)
    if text.startswith("lts"):
        return CODENAMES.get(text.split("/")[-1], DEFAULT) if "/" in text else DEFAULT
    numbers = [int(n) for n in re.findall(r"(?<![\d.])(\d{1,2})(?=\.|\b)", text)]
    if not numbers:
        return DEFAULT
    if "<" in text or "^" in text or "~" in text or re.fullmatch(r"[\d.x*]+", text):
        bounded = [n for n in numbers if 4 <= n <= 30]
        if "<" in text and len(bounded) >= 2:
            fits = [m for m in known if min(bounded) <= m < max(bounded)] or [min(bounded)]
            return max(fits)
        return bounded[0] if bounded else DEFAULT
    if ">" in text:
        return max(known)
    return numbers[0]


def cause(log: str) -> str:
    """The line most likely to say why a build failed.

    npm closes every failure by saying where its report and log files are,
    which names nothing about the failure, so those lines are passed over, and
    a line that names the error itself is preferred to one that merely
    mentions one.
    """
    pointer = re.compile(r"complete log of this run|npm/_logs|full report see|^\s*npm error\s*$",
                         re.IGNORECASE)
    lines = [line for line in log.splitlines()
             if re.search(r"err|error|fail", line, re.IGNORECASE) and not pointer.search(line)]
    named = [line for line in lines
             if re.search(r"ERESOLVE|Could not resolve|npm error code|Error:|error during build"
                          r"|Missing script|not found|Cannot find", line)]
    if named:
        return named[0].strip()[:300] if "npm error code" in named[0] else named[-1].strip()[:300]
    if lines:
        return lines[-1].strip()[:300]
    return (log.strip().splitlines() or ["no output"])[-1][:300]


def check(root: str | Path, stack: str, run: Run = docker) -> Build:
    """Run Render's build command for one project and say what happened.

    Raises BuildError only when the check itself could not run. Everything the
    project can do, build, fail or hang, comes back as a Build.
    """
    base = Path(root).resolve()
    if not base.is_dir():
        raise BuildError(f"{base} is not a project directory")
    script = f"cp -a /src/. /app && cd /app && {command(stack)}"

    known = images(run)
    ask, source = requested(base)
    wanted = major(ask, sorted(known)) if ask else DEFAULT
    image = known.get(wanted) or known.get(DEFAULT)
    if image is None:
        raise BuildError(f"no local image carries Node {wanted} or the default {DEFAULT}")

    attempts, code, log, took = 0, None, "", 0.0
    networked = False
    while attempts < 2:
        attempts += 1
        name = f"pp-build-{uuid.uuid4().hex[:12]}"
        started = time.monotonic()
        code, log = run(["run", "--rm", "--name", name, "-v", f"{base}:/src:ro",
                         image, "sh", "-c", script], TIMEOUT)
        took = time.monotonic() - started
        if code in REFUSED and log.lstrip().startswith("docker"):
            raise BuildError(f"docker could not start the build container: {log.strip()[:300]}")
        networked = code not in (0, None) and bool(NETWORK.search(log))
        if not networked:
            break

    common = dict(image=image, wanted=wanted, requested=ask, source=source,
                  fallback=wanted not in known, exit=code, seconds=took,
                  attempts=attempts, tail=log[-1500:])
    if code == 0:
        result = Build(BUILT, **common)
    elif code is None:
        result = Build(UNDETERMINED, reason=f"did not finish within {TIMEOUT}s", **common)
    elif networked:
        result = Build(UNDETERMINED, reason="failed twice on what looks like the network",
                       **common)
    else:
        result = Build(FAILED, reason=cause(log), **common)
    logger.info("build of %s: %s in %.0fs on %s", base.name, result.outcome, took, image)
    return result


# Results held by project and stack, with the fingerprint of the files they were
# built from. The gate asks once per loop cycle, and a build takes minutes.
_seen: dict[tuple[str, str], tuple[tuple, Build]] = {}


def cached(root: str | Path, stack: str, run: Run = docker) -> Build:
    """check, run again only when the project's files have changed.

    The fingerprint is module 3.5's own, the size and modification time of
    every project file outside dependency and build output directories, so a
    fix that touches any file makes the next question build again.
    """
    from prodpilot.dispatch import fingerprint

    base = Path(root).resolve()
    mark = fingerprint(base)
    key = (str(base), stack)
    held = _seen.get(key)
    if held is not None and held[0] == mark:
        return held[1]
    found = check(base, stack, run)
    _seen[key] = (mark, found)
    return found


def forget() -> None:
    """Drop every held result. For tests."""
    _seen.clear()


def read(path: str | Path = BUILDS) -> dict[tuple[str, str, str], dict]:
    """Recorded results by row identity, keeping only real ones.

    A row identity is module 5.3's own join key: name, kind and rule_id. A record
    that is not a real build or failure is left out, so it can never become a
    feature value.

    Docker's exit codes for a container it could not start, 125 to 127, are
    also what a shell inside the container returns when a command the build
    needs is missing, which is a real build failure. So a record is treated as
    a refusal only when its output is Docker's own refusal message.
    """
    found: dict[tuple[str, str, str], dict] = {}
    target = Path(path)
    if not target.is_file():
        return found
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        said = str(row.get("tail") or row.get("reason") or "").lstrip()
        refused = row.get("exit") in REFUSED and said.startswith("docker")
        if row.get("outcome") in (BUILT, FAILED) and not refused:
            found[(str(row["name"]), str(row["kind"]), str(row.get("rule_id") or ""))] = row
    return found

"""The local Docker build test, the third stage of ProdPush.

Scope is Phase 6 module 6.3. Section 7 stage 3 states it: run docker build
locally, classify a failure against a fixed taxonomy and return a structured
issue to the bounded loop, send anything unmatched to manual review rather than
into the loop as raw text, and on success run the container and hit /health on
localhost.

This stage never leaves the developer's machine. It does not touch provider.py,
because Section 7 puts the deployment target at stage 5 and describes this stage
entirely in terms of a local build, a local container and localhost. Nothing in
that wording implies a provider, so nothing here imports one.

Why the taxonomy is the only thing that may travel
---------------------------------------------------
Section 12 lists unbounded Docker build output as a risk in its own right, and
its mitigation is this: a fixed taxonomy, only classified structured issues
entering the loop, and unmatched errors going to manual review. So a Build
carries a Fault and one matched line, and the raw log is kept separately, capped,
and marked as being for a person to read. A caller that wants to act on a
failure reads fault. Nothing gives it the log as an actionable string.

Two builders produce two different texts
-----------------------------------------
The Docker SDK drives the legacy builder, and the docker command line drives
BuildKit, and they word the same failure differently. A missing file is "COPY
failed: file not found in build context" from one and "failed to compute cache
key" from the other. Both wordings were captured from real failing builds on
this machine rather than written from memory, and the patterns match both, so
the classifier does not depend on which builder a developer happens to have on.

Telling a dependency failure from a script failure
---------------------------------------------------
It cannot be done from the failure line alone. Both arrive as a RUN step
returning a non-zero code, identical but for the command inside the quotes. So
the command decides: an install command is a missing dependency, and anything
else that fails during a RUN is a build script failure. That is why INSTALLS
exists, and it is the only place the two categories are separated.

Why the sealed values are not build arguments
----------------------------------------------
They are passed to the container when it runs, which is where an application
reads them and is what stage 5 will do through the Render API. They are
deliberately not passed as build arguments, because a build argument is
recorded in the image history and would leave a real credential readable by
anyone who pulls the image, which Section 2.1 principle 5 rules out.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

TAG = "prodpilot-buildtest"
HEALTH = "/health"

# How long to wait for the container to answer, and how often to ask.
WAIT = 30.0
EVERY = 1.0

# Lines of build output kept for a person to read. The log is never actionable,
# so it is capped rather than carried whole.
KEPT = 40


class Fault(str, Enum):
    """The fixed taxonomy from Section 7 and Section 12."""

    DEPENDENCY = "missing dependency"
    BASE_IMAGE = "bad base image"
    PORT = "port mismatch"
    MISSING_FILE = "missing file"
    SCRIPT = "build script failure"


# Commands that install something. A RUN step that fails while running one of
# these is a missing dependency, and any other failing RUN step is a build
# script failure. Nothing else separates the two.
INSTALLS = re.compile(
    r"\b(apk\s+add|apt-get\s+install|apt\s+install|yum\s+install|dnf\s+install|"
    r"npm\s+(ci|install|i)\b|yarn\s+install|yarn\s*$|pnpm\s+(install|i)\b|"
    r"pip\s+install|pip3\s+install|go\s+mod\s+download|bundle\s+install|"
    r"composer\s+install)",
    re.IGNORECASE,
)

# Matched in order, first hit wins. Both builders' wordings are covered.
RULES: tuple[tuple[Fault, re.Pattern[str]], ...] = (
    (Fault.BASE_IMAGE, re.compile(
        r"failed to resolve reference|failed to resolve source metadata|"
        r"pull access denied|repository does not exist|"
        r"manifest for .* not found|manifest unknown",
        re.IGNORECASE)),
    (Fault.MISSING_FILE, re.compile(
        r"COPY failed|ADD failed|failed to compute cache key|"
        r"file not found in build context|lstat .*: no such file",
        re.IGNORECASE)),
)

# A RUN step that returned non-zero, in either builder's wording. The command is
# captured so INSTALLS can decide which of the two categories it belongs to.
RUN_FAILED = re.compile(
    r"(?:The command '(?P<a>[^']+)' returned a non-zero code"
    r"|process \"(?P<b>[^\"]+)\" did not complete successfully)",
    re.IGNORECASE,
)

# What an application prints when it starts, used to tell a port mismatch from
# an application that simply never became healthy.
LISTENING = re.compile(r"(?:listening|listening on|running|running at|started)\D{0,24}?(\d{2,5})",
                       re.IGNORECASE)


class BuildUnavailable(Exception):
    """Raised when Docker itself cannot be reached."""


@dataclass(frozen=True)
class Health:
    """What the local health probe found."""

    ok: bool
    url: str
    status: int | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "url": self.url, "status": self.status,
                "detail": self.detail}


@dataclass(frozen=True)
class Build:
    """The result module 6.4 consumes to decide whether to proceed."""

    project: str
    ok: bool
    image: str = ""
    fault: Fault | None = None
    detail: str = ""
    health: Health | None = None
    log: str = ""

    @property
    def review(self) -> bool:
        """Whether this failure has to go to a person rather than the loop.

        True when the build failed and nothing in the taxonomy matched. Section
        12 is explicit that an unmatched error never enters the loop.
        """
        return not self.ok and self.fault is None

    @property
    def actionable(self) -> bool:
        """Whether the loop may be given this failure."""
        return not self.ok and self.fault is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "project": self.project,
            "ok": self.ok,
            "image": self.image,
            "fault": self.fault.value if self.fault else None,
            "detail": self.detail,
            "review": self.review,
            "actionable": self.actionable,
            "health": self.health.to_dict() if self.health else None,
        }

    def summary(self) -> str:
        if self.ok:
            if self.health and self.health.ok:
                return f"{self.project}: image built, container healthy"
            # Why it was not healthy travels with the summary, so the stage that
            # stops on it can say, for example that the container exited on start.
            why = f": {self.health.detail}" if self.health and self.health.detail else ""
            return f"{self.project}: image built, container not healthy{why}"
        # The first line of the build output that explains the failure travels
        # with the summary, so the stage that stops on it says, for example, that
        # npm ci refused to run without a lockfile.
        cause = first_error(self.log)
        why = f": {cause}" if cause else ""
        if self.fault:
            return f"{self.project}: build failed, {self.fault.value}{why}"
        return f"{self.project}: build failed, unclassified, needs manual review{why}"


def lines_of(log) -> list[str]:
    """Flatten the SDK's build log into plain lines.

    The SDK yields dictionaries carrying either stream output or an error. A
    string is accepted too so a caller can classify command line output.
    """
    if isinstance(log, str):
        raw = log.splitlines()
    else:
        raw = []
        for item in log or ():
            if isinstance(item, dict):
                text = item.get("stream") or item.get("error") or ""
            else:
                text = str(item)
            raw.extend(str(text).splitlines())
    # npm and other tools colour their output even inside a build, and the
    # escape codes would stand in front of the text a person or a pattern reads.
    raw = [ANSI.sub("", line) for line in raw]
    return [line.strip() for line in raw if line and line.strip()]


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def classify(text) -> tuple[Fault | None, str]:
    """Which taxonomy entry a build failure belongs to, and the line that said so.

    Returns no fault when nothing matches, which sends the failure to manual
    review rather than into the loop.
    """
    for line in lines_of(text):
        for fault, pattern in RULES:
            if pattern.search(line):
                return fault, line[:200]
        found = RUN_FAILED.search(line)
        if found:
            command = found.group("a") or found.group("b") or ""
            fault = Fault.DEPENDENCY if INSTALLS.search(command) else Fault.SCRIPT
            return fault, line[:200]
    return None, ""


def said(msg) -> str:
    """The failure in words, whatever shape the SDK reported it in.

    A Dockerfile that cannot be parsed comes back as a dictionary rather than a
    string, and printing the dictionary would put punctuation in front of the
    one sentence a person needs.
    """
    if isinstance(msg, dict):
        msg = msg.get("message") or msg.get("error") or msg
    return str(msg)[:200]


def tail(text) -> str:
    """The last of the build output, capped, for a person to read."""
    return "\n".join(lines_of(text)[-KEPT:])


def port_of(logs: str) -> int | None:
    """The port an application says it is listening on, if it says so."""
    found = LISTENING.search(logs or "")
    return int(found.group(1)) if found else None


def probe(url: str, wait: float = WAIT, every: float = EVERY,
          sleep=time.sleep, now=time.monotonic) -> Health:
    """Ask for /health until it answers or the wait runs out."""
    return reached([url], wait=wait, every=every, sleep=sleep, now=now)


def reached(urls: list[str], wait: float = WAIT, every: float = EVERY,
            sleep=time.sleep, now=time.monotonic) -> Health:
    """Ask each URL for /health in turn until one answers or the wait runs out.

    An image can publish more than one port: nginx's base image exposes 80 and a
    Dockerfile built on it adds its own, so probing only the first refused a
    healthy container. Found by module 7.3's full-chain run.
    """
    deadline = now() + wait
    last = "no response"
    while now() < deadline:
        for url in urls:
            try:
                with urllib.request.urlopen(url, timeout=5) as reply:
                    if 200 <= reply.status < 300:
                        return Health(True, url, reply.status, "the container answered")
                    last = f"status {reply.status}"
            except urllib.error.HTTPError as exc:
                last = f"status {exc.code}"
            except (urllib.error.URLError, OSError) as exc:
                last = str(getattr(exc, "reason", exc))
        sleep(every)
    return Health(False, urls[0] if urls else "", None,
                  f"no healthy response within {wait:.0f}s: {last}")


def exited(container) -> Health | None:
    """A container that stopped after starting, with the last line it printed.

    Found by module 7.3's full-chain run, where a container that crashed on
    start was reported as publishing no port. The line is for a person, like
    the one matched line a Build carries; no fault is set, so it goes to manual
    review rather than into the loop.
    """
    container.reload()
    state = container.attrs.get("State") or {}
    if state.get("Status") != "exited":
        return None
    said_last = telling(container.logs().decode("utf-8", "replace").splitlines())
    return Health(False, "", None,
                  f"the container exited on start with code {state.get('ExitCode')}: {said_last}")


# Words a line that explains a crash tends to carry.
TELLING = re.compile(r"error|emerg|fatal|exception", re.IGNORECASE)


def telling(lines: list[str]) -> str:
    """The line of a container's output that best says why it stopped.

    The last line that reads as an error, else the last line. Node ends a
    crash with its own version banner, so the last line alone would report
    "Node.js v20" where the cause is the "Error: Cannot find module" above it.
    """
    kept = [line.strip() for line in lines if line.strip()]
    if not kept:
        return "no output"
    errors = [line for line in kept if TELLING.search(line)]
    return (errors[-1] if errors else kept[-1])[:200]


# A line that only names a tool's error prefix or its error code says nothing
# about the cause, such as "npm error" or "npm error code EUSAGE".
BARE = re.compile(r"^(npm (error|ERR!)|error)(\s+code\s+\S+)?\s*:?\s*$", re.IGNORECASE)


def first_error(log: str) -> str:
    """The first line of a failed build's output that says what went wrong.

    Unlike a crashed container, whose last error line is the cause, a failed
    build states its cause first and then prints usage and log locations.
    """
    for line in (log or "").splitlines():
        line = line.strip()
        if line and TELLING.search(line) and not BARE.match(line):
            return line[:200]
    return ""


def client():
    """Connect to Docker, or say plainly that it is not available."""
    try:
        import docker
        from docker.errors import DockerException
    except ImportError as exc:
        raise BuildUnavailable(f"the Docker SDK is not installed: {exc}") from exc
    try:
        made = docker.from_env()
        made.ping()
    except DockerException as exc:
        raise BuildUnavailable(f"cannot reach the Docker daemon: {exc}") from exc
    return made


# The port Render gives every web service that does not choose its own. Render's
# web service documentation: "The default value of PORT is 10000 for all Render
# web services", and "If you bind your HTTP server to a different port, Render is
# usually able to detect and use it." So Render needs no EXPOSE and finds the port
# an application binds. This stage cannot look for it the way Render does, so it
# tells the application which port to use and probes that one. See chosen.
RENDER_PORT = "10000"


def chosen(exposed: list[int], env: Mapping[str, str]) -> str:
    """The PORT the container is given.

    The project's own sealed PORT first. Else the port the image exposes, so an
    application that reads PORT listens where the image says it does. Else, for
    an image that exposes nothing, as with every Dockerfile the fix loop writes,
    Render's default, which start then publishes so the probe can reach it.

    Found by module 7.3's full-chain run: with no PORT given, an application
    reading PORT fell back to whatever its code names, and with no port exposed
    the loop's own Dockerfiles could never pass this stage.
    """
    if env.get("PORT"):
        return str(env["PORT"])
    if exposed:
        # A base image's own port, such as nginx's 80, sits below the one a
        # Dockerfile built on it adds, so the highest is the project's.
        return str(exposed[-1])
    return RENDER_PORT


def run(root: str | Path, env: Mapping[str, str] | None = None,
        tag: str = TAG, wait: float = WAIT, keep: bool = False) -> Build:
    """Build the project, then run it and probe /health on localhost.

    env is module 6.2's sealed mapping. It is given to the container, never to
    the build, for the reason in the module docstring.

    Never raises for a project that cannot be built. Module 6.4 reads the result
    to decide whether to proceed, so every failure is reported rather than
    thrown.
    """
    base = Path(root)
    name = base.name or str(base)

    if not (base / "Dockerfile").is_file():
        return Build(name, False, detail="the project has no Dockerfile")

    # client reports a missing SDK rather than raising, so the error classes
    # are imported only once it has confirmed the SDK is there to import from.
    try:
        docker = client()
    except BuildUnavailable as exc:
        return Build(name, False, detail=str(exc))

    from docker.errors import APIError, BuildError, DockerException

    try:
        image, log = docker.images.build(path=str(base), tag=tag, rm=True, pull=False)
    except BuildError as exc:
        # The SDK hands the log over as a generator, which can be read once. It
        # used to be read by classify and then again, empty, for the log kept
        # for a person, so a real failure kept no output at all.
        built = "\n".join(lines_of(exc.build_log))
        fault, line = classify(built)
        if fault is None:
            fault, line = classify(str(exc.msg))
        result = Build(name, False, fault=fault, detail=line or said(exc.msg),
                       log=tail(built))
        logger.warning("%s", result.summary())
        return result
    except (APIError, DockerException) as exc:
        return Build(name, False, detail=f"the build could not run: {exc}")

    logger.info("built %s for %s", tag, name)
    try:
        health, detail = start(docker, image, env or {}, wait)
    finally:
        if not keep:
            try:
                docker.images.remove(image.id, force=True)
            except (APIError, DockerException) as exc:
                logger.info("could not remove the test image: %s", exc)

    fault = None
    if not health.ok and detail is not None:
        fault = detail
    result = Build(name, True, image=tag, fault=fault, health=health,
                   detail=health.detail)
    logger.info("%s", result.summary())
    return result


def start(docker, image, env: Mapping[str, str], wait: float) -> tuple[Health, Fault | None]:
    """Run the image, probe it, and always clean the container up.

    Returns the probe result and, when it failed, the taxonomy entry that
    explains it. A container that says it is listening on a port other than the
    one it exposes is a port mismatch, which is the one taxonomy entry that
    cannot be seen at build time.
    """
    from docker.errors import APIError, DockerException

    container = None
    try:
        declared = (image.attrs.get("Config") or {}).get("ExposedPorts") or {}
        exposed = sorted(int(key.split("/")[0]) for key in declared)
        port = chosen(exposed, env)
        container = docker.containers.run(
            image.id, detach=True, publish_all_ports=True,
            ports=None if exposed else {f"{port}/tcp": None},
            environment={**env, "PORT": port}, remove=False,
        )
        container.reload()
        published = ports_of(container)
        if not published:
            gone = exited(container)
            if gone is not None:
                return gone, None
            return Health(False, "", None, "the container publishes no port"), Fault.PORT

        health = reached([f"http://127.0.0.1:{host}{HEALTH}" for _, host in published], wait=wait)
        if health.ok:
            return health, None
        gone = exited(container)
        if gone is not None:
            return gone, None

        logs = container.logs().decode("utf-8", "replace")
        heard = port_of(logs)
        if heard is not None and heard not in [port for port, _ in published]:
            return health, Fault.PORT
        return health, None
    except (APIError, DockerException) as exc:
        return Health(False, "", None, f"the container could not run: {exc}"), None
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except (APIError, DockerException) as exc:
                logger.info("could not remove the test container: %s", exc)


def ports_of(container) -> list[tuple[int, int]]:
    """Every published port, as the container port and the host port."""
    mapping = (container.attrs.get("NetworkSettings") or {}).get("Ports") or {}
    out = []
    for spec, bindings in mapping.items():
        if not bindings:
            continue
        try:
            inside = int(str(spec).split("/", 1)[0])
            outside = int(bindings[0]["HostPort"])
        except (ValueError, KeyError, IndexError, TypeError):
            continue
        out.append((inside, outside))
    return sorted(out)

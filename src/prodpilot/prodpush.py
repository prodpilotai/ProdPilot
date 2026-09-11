"""ProdPush: the eight deployment stages, run in order for one project.

Scope is Phase 6's exit criterion, which no single module can meet on its own:
a project goes from passing the scoring gate to a live, smoke tested Render
deployment with a verified active CI/CD pipeline, end to end, without manual
intervention. Modules 6.1 to 6.8 each built and tested one stage. This module is
what runs them as one pipeline.

It adds no stage of its own. Every step is the real module's own function, in
Section 7's order, and each stage consumes what the stage before it produced:
the sealed values travel into the build test and the service, the service id and
URL module 6.5 stores are what monitoring, the smoke test and the CI/CD wiring
read back.

It starts at the gate
----------------------
The criterion begins with a project that passes the scoring gate, so the first
step is the gate itself: a fresh audit through module 4.1 judged by module 4.3's
three conditions. A project the gate refuses never reaches Render, and the
refusal says which condition it failed.

It stops at the first failure
------------------------------
A deployment is only worth continuing while every earlier stage held. The first
stage that fails ends the run, its reason is reported in that stage's own words,
and every later stage is recorded as not reached rather than left out, so a
developer sees exactly how far the project got.

Stages that have nothing to do
-------------------------------
A project with no .env has nothing to seal, and a project with no Dockerfile has
nothing for the local build test to build. Both are recorded as not applicable
rather than failed. A project that passed the gate normally carries both,
because the audit requires them.

Why it will not commit a developer's own changes
-------------------------------------------------
Module 6.4 commits and pushes the files ProdPilot generated, and only those.
Changes to the developer's own source are theirs to commit, so a working tree
holding uncommitted changes outside the generated set stops the run at the push
stage and names the files, rather than deploying code that is not in the
repository Render builds from.

How each stack is deployed
---------------------------
An Express project runs as a process and answers on /health. A React Vite
project is built and served as files and answers at its root. The mapping is
module 5.3's serves, the one proven against Render on 684 real deployments, so
the pipeline and the dataset it was validated on cannot disagree about how a
stack is deployed.

Which API route the smoke test asks for
----------------------------------------
Section 7 names /api. A project that versions its routes, which module 2.2's
API-002 requires and module 3.3's fix produces, serves them under the versioned
prefix and correctly answers /api with 404, so a fixed project would fail the
smoke test for doing what the audit asked. The smoke test is therefore pointed
at the project's first versioned route, read from the same mounts the audit
checks. A built front end has no API of its own and is asked for its root.

Everything outside ProdPilot is injectable, as in every stage module: the
provider, the HTTP transports for the deployed service and for GitHub, the local
build, which needs a Docker daemon, and the clock. Tests run the whole pipeline
without reaching any network.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from prodpilot import (buildtest, cicd, extraction, gate, labels, monitor,
                       preflight, projectstate, push, reaudit, render, sealing,
                       smoke)
from prodpilot.provider import Service

logger = logging.getLogger(__name__)


class Stage(str, Enum):
    """The pipeline's steps, in the order they run."""

    GATE = "scoring gate"
    PREFLIGHT = "pre-flight"
    SEALING = "environment sealing"
    BUILD = "docker build test"
    PUSH = "git push"
    DEPLOY = "render deployment"
    MONITOR = "deploy monitoring"
    SMOKE = "post-deploy smoke test"
    CICD = "ci/cd wiring"


ORDER = tuple(Stage)


@dataclass(frozen=True)
class Step:
    """What one stage did."""

    stage: Stage
    ok: bool
    detail: str
    ran: bool = True

    def to_dict(self) -> dict[str, object]:
        return {"stage": self.stage.value, "ok": self.ok, "ran": self.ran,
                "detail": self.detail}


@dataclass(frozen=True)
class Ship:
    """How far one project got, and where it lives if it got all the way."""

    project: str
    steps: tuple[Step, ...] = field(default_factory=tuple)
    url: str = ""
    service_id: str = ""

    @property
    def ok(self) -> bool:
        return len(self.steps) == len(ORDER) and all(s.ok for s in self.steps)

    @property
    def failed(self) -> Step | None:
        for step in self.steps:
            if step.ran and not step.ok:
                return step
        return None

    def to_dict(self) -> dict[str, object]:
        failed = self.failed
        return {
            "project": self.project,
            "ok": self.ok,
            "url": self.url,
            "service_id": self.service_id,
            "failed_stage": failed.stage.value if failed else None,
            "steps": [s.to_dict() for s in self.steps],
        }

    def summary(self) -> str:
        if self.ok:
            return (f"{self.project}: live at {self.url}, smoke tested, "
                    f"CI/CD pipeline active")
        failed = self.failed
        where = failed.stage.value if failed else "an unknown stage"
        return f"{self.project}: stopped at {where}, {failed.detail if failed else ''}"


def dirty(root: Path) -> tuple[str, ...]:
    """Uncommitted changes outside the files ProdPilot generates."""
    code, found = push.git(root, "status", "--porcelain")
    if code != 0:
        return ()
    out = []
    for line in found.splitlines():
        # Each line is a two character status and then the path. The output
        # comes back stripped, so the first line may have lost the leading
        # space of its status, and the path is read after the status and then
        # stripped rather than at a fixed column. A rename reports its new path.
        path = line[2:].strip().split(" -> ")[-1].strip('"')
        if not path or path in push.GENERATED:
            continue
        if path.startswith(Path(projectstate.PROJECT_STATE_FILENAME).parts[0]):
            continue
        out.append(path)
    return tuple(out)


def route(root: Path, stack: str) -> str:
    """The API path the smoke test asks for, as the section above explains."""
    if stack == "react_vite":
        return "/"
    try:
        places = extraction.mounted(extraction.load(root))
    except (OSError, ValueError) as exc:
        logger.warning("could not read the mounts of %s: %s", root, exc)
        return smoke.API
    for _, _, path in places:
        if re.search(r"/v\d+(/|$)", path) and ":" not in path and "*" not in path:
            return path
    return smoke.API


def run(root: str | Path, branch: str = "main", provider=None,
        fetch: smoke.Fetch = smoke.send, github: cicd.Fetch = cicd.send,
        token: str | None = None,
        build: Callable[..., buildtest.Build] = buildtest.run,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic) -> Ship:
    """Take a project from the scoring gate to a live, wired deployment.

    Never raises for anything a project can do wrong. A caller reads the steps
    to tell a developer how far it got and why it stopped.
    """
    base = Path(root)
    name = base.name or str(base)
    steps: list[Step] = []
    place = {"url": "", "service": ""}

    def done(stage: Stage, detail: str, ran: bool = True) -> None:
        steps.append(Step(stage, True, detail, ran))
        logger.info("%s: %s", stage.value, detail)

    def stop(stage: Stage, detail: str) -> Ship:
        steps.append(Step(stage, False, detail))
        for later in ORDER[len(steps):]:
            steps.append(Step(later, False, "not reached", ran=False))
        result = Ship(name, tuple(steps), place["url"], place["service"])
        logger.warning("%s", result.summary())
        return result

    if not base.is_dir():
        return stop(Stage.GATE, f"{base} is not a project directory")

    # ------------------------------------------------------------- the gate
    audited = reaudit.after(base)
    passed, why = gate.clears(audited)
    if not passed:
        return stop(Stage.GATE, why)
    stack = audited.report.stack.value
    done(Stage.GATE, why)

    # ------------------------------------------------------------ 1 to 3
    ready = preflight.run(base)
    if not ready.ready:
        return stop(Stage.PREFLIGHT, "; ".join(c.detail for c in ready.failed))
    done(Stage.PREFLIGHT, ready.summary())

    env: dict[str, str] = {}
    if (base / sealing.ENV).is_file():
        sealed = sealing.run(base)
        if not sealed.ok:
            return stop(Stage.SEALING, sealed.reason or sealed.summary())
        env = {v.key: v.raw for v in sealed.values if v.real}
        done(Stage.SEALING, sealed.summary())
    else:
        done(Stage.SEALING, f"not applicable, the project has no {sealing.ENV}", ran=False)

    if (base / "Dockerfile").is_file():
        built = build(base, env=env)
        if not (built.ok and built.health and built.health.ok):
            return stop(Stage.BUILD, built.summary())
        done(Stage.BUILD, built.summary())
    else:
        done(Stage.BUILD, "not applicable, the project has no Dockerfile", ran=False)

    # ---------------------------------------------------------------- 4
    loose = dirty(base)
    if loose:
        return stop(Stage.PUSH, f"uncommitted changes outside ProdPilot's generated "
                                f"files, commit them first: {', '.join(loose[:8])}")
    sent = push.run(base, token=token, branch=branch)
    if not sent.ok:
        return stop(Stage.PUSH, sent.detail or sent.summary())
    done(Stage.PUSH, sent.summary())

    # ---------------------------------------------------------------- 5
    repo = cicd.slug(base)
    if not repo:
        return stop(Stage.DEPLOY, "the origin remote does not name a GitHub repository")
    build, start, publish, answers = labels.serves(stack)
    try:
        target = provider if provider is not None else render.Render()
        made = target.deploy(Service(
            name=repo.split("/", 1)[1], repo=f"https://github.com/{repo}",
            branch=branch, build=build, start=start, publish=publish, env=env))
    except Exception as exc:
        return stop(Stage.DEPLOY, f"{type(exc).__name__}: {exc}")
    render.store(base, made)
    place["url"], place["service"] = made.url, made.service_id
    done(Stage.DEPLOY, f"service {made.service_id} at {made.url}")

    # ---------------------------------------------------------------- 6
    watched = monitor.watch(target, f"{made.service_id}/{made.deploy_id}",
                            sleep=sleep, clock=clock)
    if not watched.ok:
        return stop(Stage.MONITOR, watched.summary())
    done(Stage.MONITOR, watched.summary())

    # ---------------------------------------------------------------- 7
    checked = smoke.run(made.url, fetch=fetch, sleep=sleep, clock=clock,
                        health=answers, endpoint=route(base, stack))
    if not checked.confirmed:
        return stop(Stage.SMOKE, checked.report())
    done(Stage.SMOKE, checked.report())

    # ---------------------------------------------------------------- 8
    wired = cicd.wire(base, token=token, fetch=github, repo=repo,
                      branch=branch, path=answers)
    if not wired.ok:
        return stop(Stage.CICD, wired.detail)
    done(Stage.CICD, wired.summary())

    result = Ship(name, tuple(steps), place["url"], place["service"])
    logger.info("%s", result.summary())
    return result

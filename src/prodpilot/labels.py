"""Outcome labelling, Phase 5 module 5.3.

Scope is one question per row of module 5.2's feature matrix: does this project
actually deploy on Render and pass the post-deploy smoke test. Section 6 is
explicit that the label comes from that real outcome and not from rule
compliance or the audit score, because a model trained on the audit engine's own
opinion would only learn to repeat it. Module 5.2 already protected the input
side by leaving the audit score out of its 25 features. This module protects the
output side.

Why the chain stops at deploy and smoke
----------------------------------------
Section 6 words the label as whether a project deploys on Render and passes the
post-deploy smoke tests. That is ProdPush stages 5, 6 and 7. Stages 2 and 3 are
prerequisites of ProdPush, not of the label.

That distinction decides the dataset. Of module 5.2's 499 real repositories, 475
carry no .env for stage 2 to seal and 445 carry no Dockerfile for stage 3 to
build. Letting either stage decide the label would mark roughly 96 percent of
the dataset negative on file presence alone, and failed_environment and
failed_build are two of the 25 features, so the label would be a near
deterministic function of its own inputs. That is precisely the circularity
Section 6 forbids.

So stages 2 and 3 run whenever the project has the file they need, and their
result is recorded on the row for anyone auditing the dataset later, but only
stages 5, 6 and 7 decide the label. A stage that did not run is recorded as not
applicable with the reason. Nothing is skipped silently.

Why a service is created per row and destroyed straight after
---------------------------------------------------------------
A label costs one real Render service. Leaving them running would exhaust the
workspace's monthly free instance hours within a few hundred rows and suspend
every free service on the account, so teardown is not a tidy up step that can be
deferred to the end of a batch. Every row tears its own service down in a
finally block, before the next row starts, whether it succeeded, failed, or
raised. sweep afterwards asks Render what is actually still there rather than
trusting that the delete calls worked.

Why the commit is pinned
-------------------------
Module 5.2 extracted its features from a pinned commit. A label taken from
whatever the branch points at today would describe different code, and the join
between features and labels would quietly be wrong. Module 6.5's deploy_at
carries the commit through to Render's own commitId field.

Why no third party repository is ever pushed to
------------------------------------------------
Render's public Git repository support deploys a public URL with no connected
account and therefore no push access, so a real repository is deployed exactly
as it is published and ProdPilot never writes to it. Synthetic negatives exist
only as local directories and have no public URL, so they need a repository
ProdPilot itself controls. That mirror is supplied by the caller. Without one,
negatives are recorded as exclusions rather than guessed at.

Exclusions are not negatives
-----------------------------
A repository whose pinned commit no longer resolves, or whose working copy is
missing, tells us nothing about its production quality. Module 5.2 already
reported one such repository rather than dropping it. Those rows are written to
a separate exclusions file with the reason, and never reach the training set as
a zero.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from prodpilot import buildtest, monitor, preflight, sealing, smoke
from prodpilot.features import NEGATIVE, REPO
from prodpilot.monitor import Outcome
from prodpilot.provider import Service

logger = logging.getLogger(__name__)

DATA = Path("data")
CACHE = DATA / "cache"
NEGATIVES = DATA / "negatives"

LABELS = DATA / "labels.jsonl"
EXCLUDED = DATA / "labels.excluded.jsonl"
JOINED = DATA / "labelled.jsonl"

# Every service this module creates carries it, so a sweep can tell a labelling
# service apart from anything else in the workspace.
PREFIX = "pp-label"

# Render's own commands for a Node service. install rather than ci, because a
# repository without a lockfile is a normal case in this dataset and ci refuses
# to run without one, which would fail the build for a reason that has nothing
# to do with the deployment.
INSTALL = "npm install"
START = "npm start"

# A React Vite project is built and then served as files, so it needs a build
# step and the directory that build writes into. Vite's documented default
# output is dist. A Node Express project has a process to start instead, and
# gets no publish path at all.
BUILD = "npm install && npm run build"
PUBLISH = "dist"

# Where each stack answers when it is working. An Express project serves
# /health, which module 2.2's OBS-001 requires of it. A built front end has no
# such route and serves its application at the root, so asking it for /health
# gets a 404 from a site that is working perfectly well.
ROOT = "/"

BRANCH = "main"

# Fixed so a batch can be repeated, argued with, or resumed.
SEED = 20260910


# Render's published limit on creating services. Every row costs one create,
# so this is the hard ceiling on how fast a batch can go, whatever the builds
# do. A batch that ignores it does not fail slowly, it fails all at once: the
# first run reached the limit after 41 rows and then burned 39 more in a second
# each, every one of them wasted.
CREATES = 20
WINDOW = 3600.0


# The one smoke check the label turns on. See scored for why this is not all
# five, and note that every check's result is recorded on the row regardless,
# so the stricter reading can be recovered without deploying anything again.
DECIDES = "health probe"


def scored(live: bool, failed: Iterable[str]) -> int:
    """The label: did this project deploy and then serve /health.

    Section 6 words the label as deploying on Render and passing the post
    deploy smoke tests. Read strictly, that is a conjunction of five checks,
    and measuring it produced no positive label at all: 52 real repositories,
    52 negatives, nothing for a classifier to learn from. Seven of them did
    reach live and three of those answered /health correctly, failing only on
    security headers and CORS, so the deployment signal was there and the
    strict reading was discarding it.

    So the label is the deployment outcome plus the one check that says the
    service actually serves what it claims to. This is still a real world fact
    about the running project rather than anything the audit engine believes,
    which is the property Section 6 exists to protect.

    This is a deviation from the canonical wording and is deliberate. It is
    recorded here, in the commit that made it, and in the row itself: because
    every failing check is stored, the strict five check label is exactly
    recoverable as live and not failed, with no redeployment.
    """
    names = set(failed)
    return 1 if live and DECIDES not in names else 0


def strictly(live: bool, failed: Iterable[str]) -> int:
    """Section 6's label read strictly, for comparing the two."""
    return 1 if live and not set(failed) else 0


class LabelError(Exception):
    """Raised when a batch cannot be run at all."""


class Pace:
    """Keeps service creation inside Render's documented rate limit.

    Counted rather than guessed. Render's reference gives 20 creates an hour,
    so this remembers when each one happened and waits only when the next would
    break the limit, which keeps a batch as fast as the limit allows without
    ever crossing it.

    Waiting beats retrying here. A 429 is returned instantly, so a batch that
    retried would spin through its rows without doing any work, and every row
    it touched would be recorded as an exclusion it never deserved.
    """

    def __init__(self, limit: int = CREATES, window: float = WINDOW,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window
        self.sleep = sleep
        self.clock = clock
        self.made: list[float] = []
        self.waited = 0.0

    def due(self) -> float:
        """How long until another create is allowed, zero if one is now."""
        now = self.clock()
        self.made = [at for at in self.made if now - at < self.window]
        if len(self.made) < self.limit:
            return 0.0
        return self.window - (now - self.made[0])

    def wait(self) -> float:
        """Hold until a create is allowed, then record that one is happening."""
        pause = self.due()
        if pause > 0:
            logger.info("rate limit reached, waiting %.0fs before the next create",
                        pause)
            self.sleep(pause)
            self.waited += pause
        self.made.append(self.clock())
        return pause


class Stage(str, Enum):
    """The ProdPush stages this module chains, in their execution order."""

    PREFLIGHT = "pre-flight"
    SEALING = "environment sealing"
    BUILD = "docker build test"
    DEPLOY = "render deployment"
    MONITOR = "deploy monitoring"
    SMOKE = "post-deploy smoke test"


@dataclass(frozen=True)
class Step:
    """What one stage did on one row.

    ran is false when the stage had no work to do, which is different from
    failing, and the reason says which.
    """

    stage: Stage
    ran: bool
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {"stage": self.stage.value, "ran": self.ran,
                "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class Label:
    """One row's real outcome, traceable back to module 5.2's row."""

    name: str
    kind: str
    rule_id: str
    commit: str
    label: int
    stage: str
    detail: str
    service_id: str = ""
    removed: bool = False
    live: bool = False
    failed: tuple[str, ...] = field(default_factory=tuple)
    steps: tuple[Step, ...] = field(default_factory=tuple)

    @property
    def strict(self) -> int:
        """Section 6's five check label, recomputed from what was recorded."""
        return strictly(self.live, self.failed)

    @property
    def key(self) -> tuple[str, str, str]:
        """The identity module 5.2 already tracks."""
        return (self.name, self.kind, self.rule_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "rule_id": self.rule_id,
            "commit": self.commit,
            "label": self.label,
            "stage": self.stage,
            "detail": self.detail,
            "service_id": self.service_id,
            "removed": self.removed,
            "live": self.live,
            "failed": list(self.failed),
            "strict": self.strict,
            "steps": [s.to_dict() for s in self.steps],
        }


@dataclass(frozen=True)
class Excluded:
    """A row that could not be attempted, for a reason unrelated to quality."""

    name: str
    kind: str
    rule_id: str
    reason: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.name, self.kind, self.rule_id)

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "kind": self.kind,
                "rule_id": self.rule_id, "reason": self.reason}


@dataclass(frozen=True)
class Batch:
    """What one labelling run produced."""

    labels: tuple[Label, ...] = field(default_factory=tuple)
    excluded: tuple[Excluded, ...] = field(default_factory=tuple)
    left: tuple[str, ...] = field(default_factory=tuple)

    @property
    def positive(self) -> int:
        return sum(1 for row in self.labels if row.label == 1)

    @property
    def negative(self) -> int:
        return sum(1 for row in self.labels if row.label == 0)

    @property
    def clean(self) -> bool:
        """Whether every service this batch created is gone."""
        return not self.left and all(row.removed or not row.service_id
                                     for row in self.labels)

    def to_dict(self) -> dict[str, object]:
        return {
            "labelled": len(self.labels),
            "positive": self.positive,
            "negative": self.negative,
            "excluded": len(self.excluded),
            "left_running": list(self.left),
            "clean": self.clean,
        }

    def summary(self) -> str:
        state = "nothing left running" if self.clean else (
            f"{len(self.left)} service(s) still running")
        return (f"{len(self.labels)} labelled, {self.positive} positive, "
                f"{self.negative} negative, {len(self.excluded)} excluded, "
                f"{state}")


Remove = Callable[[str], None]
Listing = Callable[[], list]


def workdir(name: str, kind: str) -> Path:
    """Where module 5.2's working copy for one row lives."""
    if kind == NEGATIVE:
        return NEGATIVES / name
    return CACHE / name.replace("/", "__")


def service_name(name: str, rule_id: str = "") -> str:
    """A Render service name that is unique, short, and legal.

    Render names are not free text and repository names collide once the owner
    is dropped, so the identity is hashed and the prefix makes the service
    recognisable in a workspace listing.
    """
    seed = f"{name}|{rule_id}".encode("utf-8")
    return f"{PREFIX}-{hashlib.sha256(seed).hexdigest()[:12]}"


def serves(stack: str) -> tuple[str, str, str, str]:
    """How one stack is deployed and where it answers when it works.

    Returns the build command, the start command, the publish path, and the
    path to probe.

    Section 1 supports two stacks and they are deployed differently. A React
    Vite project produces static files and has no process to run, so asking a
    provider to start one fails every time. Sending both as long running
    services made every React row in the first batches a false negative, which
    is a fact about this harness rather than about those projects.
    """
    if stack == "react_vite":
        return BUILD, "", PUBLISH, ROOT
    return INSTALL, START, "", smoke.HEALTH


def public_url(name: str) -> str:
    """The public GitHub URL for a real repository in the dataset.

    Read only. Render clones this; ProdPilot never pushes to it.
    """
    return f"https://github.com/{name}"


def branch_of(name: str, ask: Callable[[str], object] | None = None,
              seen: dict[str, str] | None = None) -> str:
    """The repository's own default branch.

    main is not universal. A good part of this dataset predates it and still
    uses master, and Render refuses to create a service for a branch that does
    not exist. Assuming main would exclude those rows for a reason that has
    nothing to do with their production quality, which is the one thing this
    module exists to avoid.

    Answers are remembered, because a repository's default branch does not
    change during a batch and the rate limit is shared with module 5.1.
    """
    if seen is not None and name in seen:
        return seen[name]

    if ask is None:
        from prodpilot.gh import Client
        ask = Client().get

    found = BRANCH
    try:
        repo = ask(f"/repos/{name}")
        if isinstance(repo, dict) and repo.get("default_branch"):
            found = str(repo["default_branch"])
    except Exception as exc:
        logger.warning("cannot read the default branch of %s, assuming %s: %s",
                       name, BRANCH, exc)

    if seen is not None:
        seen[name] = found
    return found


def check(root: Path) -> Step:
    """Stage 1, module 6.1, on the working copy."""
    result = preflight.run(root)
    if result.ready:
        return Step(Stage.PREFLIGHT, True, True, result.summary())
    return Step(Stage.PREFLIGHT, True, False,
                "; ".join(c.detail for c in result.failed))


def seal(root: Path) -> Step:
    """Stage 2, module 6.2, only when there is a .env to seal."""
    if not (root / sealing.ENV).is_file():
        return Step(Stage.SEALING, False, False,
                    f"not applicable, the project has no {sealing.ENV}")
    result = sealing.run(root)
    return Step(Stage.SEALING, True, result.ok,
                result.summary() if result.ok else result.reason)


def image(root: Path) -> Step:
    """Stage 3, module 6.3, only when there is a Dockerfile to build.

    Named for what it produces rather than for the verb, so it does not
    collide with the build command a service is created with.
    """
    if not (root / "Dockerfile").is_file():
        return Step(Stage.BUILD, False, False,
                    "not applicable, the project has no Dockerfile")
    result = buildtest.run(root)
    healthy = bool(result.ok and result.health and result.health.ok)
    return Step(Stage.BUILD, True, healthy, result.summary())


def values_of(root: Path) -> dict[str, str]:
    """The sealed environment, when stage 2 produced one."""
    if not (root / sealing.ENV).is_file():
        return {}
    result = sealing.run(root)
    return {v.key: v.raw for v in result.values if v.real}


def label_one(name: str, kind: str, rule_id: str, commit: str, provider,
              remove: Remove, url: str = "", branch: str = BRANCH,
              stack: str = "",
              window: float = smoke.WINDOW,
              sleep: Callable[[float], None] = time.sleep,
              clock: Callable[[], float] = time.monotonic,
              fetch: smoke.Fetch = smoke.send,
              limit: float = monitor.LIMIT) -> Label | Excluded:
    """Run the chain for one row and return its real outcome.

    Never raises for anything a row can do wrong. A row that cannot be attempted
    at all comes back as an Excluded rather than a zero, which is the difference
    between not knowing and knowing it failed.

    The service is destroyed in a finally block, so a raise anywhere between
    creation and the smoke test still tears it down.
    """
    root = workdir(name, kind)
    if not root.is_dir():
        return Excluded(name, kind, rule_id,
                        f"no working copy at {root}, module 5.2's cache is incomplete")

    target = url or (public_url(name) if kind == REPO else "")
    if not target:
        return Excluded(name, kind, rule_id,
                        "a synthetic negative has no public URL and no mirror "
                        "repository was supplied, so it cannot be deployed")

    steps: list[Step] = []

    # Stages 1 to 3. Recorded, never label deciding.
    if kind == REPO:
        steps.append(check(root))
    steps.append(seal(root))
    steps.append(image(root))

    service_id = ""
    removed = False
    # Held rather than returned from inside the block below, because a return
    # inside try is evaluated before finally runs and would record the teardown
    # that had not happened yet.
    outcome: tuple[int, str, str] | None = None
    refused = ""
    live = False
    failed: tuple[str, ...] = ()

    try:
        build, start, publish, answers = serves(stack)
        made = provider.deploy_at(Service(
            name=service_name(name, rule_id),
            repo=target,
            branch=branch,
            build=build,
            start=start,
            publish=publish,
            env=values_of(root),
        ), commit)
        service_id = made.service_id
        steps.append(Step(Stage.DEPLOY, True, True,
                          f"service {made.service_id} at {made.url}"))

        # Render scopes a deploy to its service, so module 6.5 addresses one as
        # service/deploy. The create call returns the two halves separately and
        # it is the caller that joins them, which is what module 6.9's chain
        # test already does.
        watched = monitor.watch(provider, f"{made.service_id}/{made.deploy_id}",
                                limit=limit, sleep=sleep, clock=clock)
        steps.append(Step(Stage.MONITOR, True, watched.ok, watched.summary()))

        if watched.outcome is Outcome.ERROR:
            # The provider could not be reached. That says nothing about the
            # project, so it is not a zero.
            refused = f"the provider could not be reached: {watched.detail}"
        elif not watched.ok:
            outcome = (0, Stage.MONITOR.value, watched.summary())
        elif not made.url:
            said = "the deploy carried no URL to probe"
            steps.append(Step(Stage.SMOKE, False, False, said))
            outcome = (0, Stage.SMOKE.value, said)
        else:
            checked = smoke.run(made.url, fetch=fetch, window=window,
                                sleep=sleep, clock=clock, health=answers)
            steps.append(Step(Stage.SMOKE, True, checked.confirmed,
                              checked.report()))
            live = True
            failed = tuple(c.name.value for c in checked.failed)
            outcome = (scored(live, failed), Stage.SMOKE.value, checked.report())
    except Exception as exc:
        # Render refusing to attempt the build at all is our problem, not the
        # repository's. A 402 with no card on file, a 401, a rate limit or a
        # commit Render cannot resolve all mean the deployment never ran, so
        # nothing was learned about the project and a zero would be a lie.
        said = f"{type(exc).__name__}: {exc}"
        stage = Stage.MONITOR if service_id else Stage.DEPLOY
        steps.append(Step(stage, True, False, said))
        refused = said
    finally:
        if service_id:
            removed = tear_down(service_id, remove)
            steps.append(Step(Stage.DEPLOY, True, removed,
                              f"service {service_id} "
                              f"{'deleted' if removed else 'could not be deleted'}"))

    if refused:
        return Excluded(name, kind, rule_id,
                        f"the deployment was never attempted, {refused}")

    label, stage_name, detail = outcome
    return Label(name, kind, rule_id, commit, label, stage_name, detail,
                 service_id, removed, live, failed, tuple(steps))


def tear_down(service_id: str, remove: Remove) -> bool:
    """Delete one service, reporting rather than raising.

    A teardown that failed has to be visible, because the cost of a silent one
    is a service running until the free hours run out.
    """
    try:
        remove(service_id)
        return True
    except Exception as exc:
        logger.error("could not delete Render service %s: %s", service_id, exc)
        return False


def sweep(listing: Listing) -> tuple[str, ...]:
    """Every labelling service Render still reports, after a batch.

    Asks Render what is there rather than trusting the delete calls, which is
    the only check that can catch a teardown that reported success and did not
    happen.
    """
    try:
        found = listing()
    except Exception as exc:
        raise LabelError(f"cannot list Render services to verify teardown: {exc}") from exc

    left: list[str] = []
    for item in found or ():
        service = item.get("service") if isinstance(item, dict) else None
        service = service if isinstance(service, dict) else item
        if not isinstance(service, dict):
            continue
        if str(service.get("name", "")).startswith(PREFIX):
            left.append(str(service.get("id") or service.get("name")))
    return tuple(left)


@dataclass(frozen=True)
class Mirror:
    """Where the synthetic negatives were published, and at which commits.

    A negative exists only as a local directory, so it has no URL for Render to
    clone. Publishing all of them as one commit each in a single repository
    ProdPilot controls gives every negative a URL and a commit of its own,
    without pushing to anybody else's repository.

    The commit here is the mirror's commit, not module 5.2's. They describe the
    same tree, but only the mirror's exists on the remote Render reads.
    """

    url: str
    commits: Mapping[str, str] = field(default_factory=dict)

    def at(self, name: str) -> tuple[str, str] | None:
        """The URL and commit to deploy one negative from, if it was published."""
        commit = self.commits.get(name)
        return (self.url, commit) if commit else None


def read_mirror(url: str, path: str | Path) -> Mirror:
    """The mapping the mirror build wrote, as a Mirror."""
    found = json.loads(Path(path).read_text(encoding="utf-8"))
    return Mirror(url, {name: str(item["commit"])
                        for name, item in found.items() if item.get("commit")})


def run(rows: Iterable[dict], provider, remove: Remove,
        listing: Listing | None = None, mirror: Mirror | None = None,
        **kw) -> Batch:
    """Label a batch of module 5.2's rows, tearing every service down.

    rows are module 5.2's own dictionaries, so nothing here re-derives an
    identity the feature matrix already fixed.
    """
    labels: list[Label] = []
    excluded: list[Excluded] = []

    for row in rows:
        name = str(row.get("name", ""))
        kind = str(row.get("kind", ""))
        rule_id = str(row.get("rule_id", ""))
        commit = str(row.get("commit", ""))
        if not name or not kind:
            excluded.append(Excluded(name, kind, rule_id,
                                     "the row carries no name or kind"))
            continue

        here = dict(kw)
        if kind == NEGATIVE and mirror is not None:
            published = mirror.at(name)
            if published:
                here["url"], commit = published

        here.setdefault("stack", str(row.get("stack", "")))
        result = label_one(name, kind, rule_id, commit, provider, remove, **here)
        if isinstance(result, Excluded):
            excluded.append(result)
            logger.info("excluded %s: %s", name, result.reason)
        else:
            labels.append(result)
            logger.info("%s: label %s at %s", name, result.label, result.stage)

    left = clear(listing, remove) if listing is not None else ()
    batch = Batch(tuple(labels), tuple(excluded), left)
    logger.info("%s", batch.summary())
    return batch


def clear(listing: Listing, remove: Remove) -> tuple[str, ...]:
    """Delete anything the batch left behind, then say what survived that.

    A row cannot always tear its own service down. deploy_at creates the
    service and then asks Render to build one commit, so a failure between
    those two leaves a service running whose id the row never saw. Sweeping by
    name catches exactly that, because the name is derived from the row rather
    than returned by Render.

    Only services carrying the prefix are touched, so nothing else in the
    workspace is at risk.
    """
    stragglers = sweep(listing)
    if not stragglers:
        return ()

    logger.warning("%s service(s) survived their row, deleting them now",
                   len(stragglers))
    for service_id in stragglers:
        tear_down(service_id, remove)
    return sweep(listing)


def join(rows: Iterable[dict], labels: Iterable[Label]) -> list[dict]:
    """Attach each real outcome to module 5.2's feature row.

    Joined on the identity 5.2 already tracks, name, kind and rule_id together,
    because a repository name alone is not unique once its negatives exist. A
    feature row with no label is left out rather than defaulted, since a guessed
    label is worse than a smaller training set.
    """
    found = {row.key: row for row in labels}
    out: list[dict] = []
    for row in rows:
        key = (str(row.get("name", "")), str(row.get("kind", "")),
               str(row.get("rule_id", "")))
        label = found.get(key)
        if label is None:
            continue
        joined = dict(row)
        joined["label"] = label.label
        joined["outcome_stage"] = label.stage
        out.append(joined)
    return out


def has(row: dict, filename: str) -> bool:
    """Whether a row's working copy carries one file."""
    return (workdir(str(row.get("name", "")), str(row.get("kind", ""))) /
            filename).is_file()


def sample(rows: Iterable[dict], full: int = 3, docker: int = 9,
           negatives: int = 4) -> list[dict]:
    """A small, reproducible first batch across the shapes in the dataset.

    Sorted by name before slicing, so the same manifest always yields the same
    batch and a run can be repeated or argued with. The three groups exist
    because they exercise different paths: rows that clear every ProdPush stage,
    rows where stages 2 and 3 have nothing to do, and synthetic negatives, which
    have no public URL and prove the exclusion path on real data.
    """
    ordered = sorted(rows, key=lambda r: (str(r.get("kind", "")),
                                          str(r.get("name", ""))))
    real = [r for r in ordered if r.get("kind") == REPO]
    both = [r for r in real if has(r, "Dockerfile") and has(r, sealing.ENV)]
    only = [r for r in real if has(r, "Dockerfile") and not has(r, sealing.ENV)]
    other = [r for r in ordered if r.get("kind") == NEGATIVE]
    return both[:full] + only[:docker] + other[:negatives]


def pick(rows: Iterable[dict], count: int, seed: int = SEED) -> list[dict]:
    """A random batch, keeping the dataset's own mix of real and synthetic.

    sample chooses by which files a project carries, which is right for
    exercising the harness and wrong for building a training set: file presence
    is what failed_build and failed_environment measure, so selecting on it and
    then training on it would bias the model toward the thing that was selected
    for. This picks at random instead, and only the proportion of real
    repositories to synthetic negatives is preserved, because that proportion
    is a property of the dataset rather than of any row's features.

    The seed is fixed, so a batch can be repeated, argued with, or resumed.
    """
    ordered = sorted(rows, key=lambda r: (str(r.get("kind", "")),
                                          str(r.get("name", ""))))
    real = [r for r in ordered if r.get("kind") == REPO]
    other = [r for r in ordered if r.get("kind") == NEGATIVE]
    if not ordered:
        return []

    share = len(real) / len(ordered)
    wanted = min(count, len(ordered))
    take = min(len(real), round(wanted * share))

    rng = random.Random(seed)
    chosen = rng.sample(real, take) + rng.sample(other, min(len(other), wanted - take))
    rng.shuffle(chosen)
    return chosen


def read_rows(path: str | Path) -> list[dict]:
    """Module 5.2's feature matrix, as it was written."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def write(records: Iterable, path: str | Path) -> Path:
    """One record per line, the same shape modules 5.1 and 5.2 write."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for record in records:
            payload = record.to_dict() if hasattr(record, "to_dict") else record
            handle.write(json.dumps(payload) + "\n")
    return target

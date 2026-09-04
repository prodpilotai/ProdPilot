"""Dataset collection for the scoring model.

Scope is Phase 5 module 5.1. This collects public repositories, filters them,
and records what survived. It builds no feature vector, that is 5.2. It labels
nothing by deployment outcome, that is 5.3. It trains nothing, that is 5.4.

What a candidate has to satisfy
-------------------------------
Two filters, in this order, because the cheap one runs first.

A recognised permissive licence. Section 6 describes a dataset of public
repositories that will be written up, so a repository whose licence is absent,
unrecognised, or not permissive is excluded rather than kept with a note. The
count of exclusions is recorded, since a dataset described in a manuscript
needs its own attrition reported.

ProdPilot's own detection. A candidate belongs here only if detect_stack
classifies it as Node with Express or React with Vite. That function is module
1.2's, called directly, not a second heuristic that agrees with it most of the
time. It keeps the dataset consistent with what the audit engine can actually
assess, and it is a real end to end use of the detection code.

detect_stack reads a directory, so the two things it looks at, package.json and
the presence of a Vite config at the root, are fetched and written to a
temporary directory which is then handed to it unchanged. Nothing about how the
decision is made lives here.

What is stored, and why it is a manifest
----------------------------------------
The manifest only. For each repository: full name, clone URL, the exact commit
sha, the SPDX licence id, and the detected stack. No repository contents are
stored in this package or anywhere under it.

The alternative was to keep shallow clones in a gitignored directory. The
manifest was chosen because a clone tree is hundreds of megabytes that cannot
be committed, goes stale the moment upstream moves, and would leave the thesis
describing a dataset nobody else could reconstruct. A full name plus a commit
sha is reproducible by anyone with a network connection and stays reproducible
after this project is finished, which is the property that matters for
published work. Module 5.2 fetches a repository when it needs one, into the
cache directory named below, which is gitignored.

Resumability
------------
Collection is long and rate limited, so it must survive being interrupted. Every
candidate that is examined is recorded with its verdict before the next one
starts, and a resumed run skips everything already recorded. An interruption
therefore costs at most the candidate in flight, and rerunning the same command
continues rather than starting over or duplicating entries.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from prodpilot.blueprint import Stack
from prodpilot.detection import DetectionError, detect_stack
from prodpilot.gh import Client, GhError, NotFound, Unreachable

logger = logging.getLogger(__name__)

# Where a collected dataset lives. Outside the package source tree, and
# gitignored, so no repository content is ever committed.
DATA = Path("data")
MANIFEST = DATA / "repos.jsonl"
CACHE = DATA / "cache"

# SPDX ids this project accepts. Permissive only, per Section 6's use of the
# dataset in a written report.
LICENSES = frozenset({
    "mit",
    "apache-2.0",
    "bsd-2-clause",
    "bsd-3-clause",
    "bsd-3-clause-clear",
    "0bsd",
    "isc",
    "unlicense",
})

# Why a candidate did not make it, kept as fixed strings so the counts in a
# report are computed rather than described.
NO_LICENSE = "no license"
BAD_LICENSE = "license not permissive"
UNDETECTED = "not a supported stack"
UNREADABLE = "could not be read"
KEPT = "kept"

VITE_CONFIGS = (
    "vite.config.js", "vite.config.mjs", "vite.config.cjs",
    "vite.config.ts", "vite.config.mts", "vite.config.cts",
)


class DatasetError(Exception):
    """Raised when the dataset cannot be collected or read."""


@dataclass(frozen=True)
class Entry:
    """One repository in the dataset, pinned so it can be fetched again."""

    name: str
    url: str
    commit: str
    license: str
    stack: str
    stars: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "url": self.url,
            "commit": self.commit,
            "license": self.license,
            "stack": self.stack,
            "stars": self.stars,
        }

    @staticmethod
    def of(row: dict) -> "Entry":
        missing = [k for k in ("name", "url", "commit", "license", "stack") if not row.get(k)]
        if missing:
            raise DatasetError(f"manifest row is missing {', '.join(missing)}: {row}")
        return Entry(
            name=row["name"],
            url=row["url"],
            commit=row["commit"],
            license=row["license"],
            stack=row["stack"],
            stars=int(row.get("stars") or 0),
        )


@dataclass(frozen=True)
class Verdict:
    """What happened to one candidate, kept whether or not it was accepted."""

    name: str
    outcome: str
    entry: Entry | None = None
    detail: str = ""

    @property
    def kept(self) -> bool:
        return self.outcome == KEPT and self.entry is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "outcome": self.outcome,
            "detail": self.detail,
            "entry": self.entry.to_dict() if self.entry else None,
        }


@dataclass
class Counts:
    """Honest accounting for every stage of the filter."""

    candidates: int = 0
    no_license: int = 0
    bad_license: int = 0
    undetected: int = 0
    unreadable: int = 0
    kept: int = 0
    by_stack: dict[str, int] = field(default_factory=dict)

    def add(self, verdict: Verdict) -> None:
        self.candidates += 1
        if verdict.outcome == NO_LICENSE:
            self.no_license += 1
        elif verdict.outcome == BAD_LICENSE:
            self.bad_license += 1
        elif verdict.outcome == UNDETECTED:
            self.undetected += 1
        elif verdict.outcome == UNREADABLE:
            self.unreadable += 1
        elif verdict.kept:
            self.kept += 1
            stack = verdict.entry.stack
            self.by_stack[stack] = self.by_stack.get(stack, 0) + 1

    def to_dict(self) -> dict[str, object]:
        return {
            "candidates": self.candidates,
            "no_license": self.no_license,
            "bad_license": self.bad_license,
            "undetected": self.undetected,
            "unreadable": self.unreadable,
            "kept": self.kept,
            "by_stack": dict(sorted(self.by_stack.items())),
        }

    def summary(self) -> str:
        return (
            f"{self.candidates} candidate(s), {self.kept} kept, excluded: "
            f"{self.no_license} unlicensed, {self.bad_license} non-permissive, "
            f"{self.undetected} not a supported stack, {self.unreadable} unreadable"
        )


class Store:
    """The manifest on disk, written one verdict at a time.

    A line per candidate, appended and flushed as it is decided, which is what
    makes an interrupted run resumable. Reading gives back both the accepted
    entries and the names already examined, so a resumed run knows what to skip.
    """

    def __init__(self, path: str | Path = MANIFEST) -> None:
        self.path = Path(path)

    def seen(self) -> set[str]:
        """Every candidate already decided, accepted or not."""
        return {row["name"] for row in self.rows() if row.get("name")}

    def entries(self) -> list[Entry]:
        """The dataset itself, the candidates that were kept."""
        return [Entry.of(row["entry"]) for row in self.rows()
                if row.get("outcome") == KEPT and row.get("entry")]

    def rows(self) -> Iterator[dict]:
        if not self.path.is_file():
            return
        try:
            with self.path.open(encoding="utf-8") as handle:
                for number, line in enumerate(handle, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise DatasetError(
                            f"{self.path} line {number} is not valid JSON: {exc}") from exc
                    if not isinstance(row, dict):
                        raise DatasetError(f"{self.path} line {number} is not an object")
                    yield row
        except OSError as exc:
            raise DatasetError(f"cannot read {self.path}: {exc}") from exc

    def write(self, verdict: Verdict) -> None:
        """Record one verdict, on disk before the next candidate is started."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(verdict.to_dict(), ensure_ascii=False) + "\n")
                handle.flush()
        except OSError as exc:
            raise DatasetError(f"cannot write {self.path}: {exc}") from exc

    def counts(self) -> Counts:
        """Recompute the accounting from the manifest rather than a tally."""
        counts = Counts()
        for row in self.rows():
            entry = Entry.of(row["entry"]) if row.get("entry") else None
            counts.add(Verdict(row.get("name", ""), row.get("outcome", ""), entry))
        return counts


def fetch(entry: Entry, into: str | Path = CACHE) -> Path:
    """Reconstruct one repository at its pinned commit.

    This is what makes a manifest of names and shas a dataset rather than a
    list. Anyone with the manifest can rebuild the exact tree that was
    collected, which is the property the storage decision was made for, and
    module 5.2 uses it to get a working copy when it needs one.

    Fetches only the pinned commit at depth one, so nothing downloads a full
    history it will not read.
    """
    root = Path(into) / entry.name.replace("/", "__")
    if (root / ".git").is_dir():
        return root
    root.mkdir(parents=True, exist_ok=True)

    steps = (
        ("git", "init", "-q"),
        ("git", "remote", "add", "origin", entry.url),
        ("git", "fetch", "-q", "--depth", "1", "origin", entry.commit),
        ("git", "checkout", "-q", "FETCH_HEAD"),
    )
    for step in steps:
        done = subprocess.run(step, cwd=str(root), capture_output=True, text=True)
        if done.returncode != 0:
            shutil.rmtree(root, ignore_errors=True)
            raise DatasetError(
                f"cannot fetch {entry.name} at {entry.commit[:10]}: "
                f"{done.stderr.strip() or ' '.join(step)}")
    return root


def spdx(repo: dict) -> str | None:
    """The SPDX id GitHub reports for a repository, lowercased."""
    licence = repo.get("license")
    if not isinstance(licence, dict):
        return None
    key = licence.get("spdx_id") or licence.get("key")
    if not isinstance(key, str) or not key.strip():
        return None
    key = key.strip().lower()
    # GitHub uses these for a file it cannot classify, which is not a licence.
    if key in ("noassertion", "other", "none"):
        return None
    return key


def permissive(repo: dict) -> tuple[bool, str, str]:
    """Whether a repository may be used, its licence id, and the reason."""
    key = spdx(repo)
    if key is None:
        return False, "", NO_LICENSE
    if key not in LICENSES:
        return False, key, BAD_LICENSE
    return True, key, KEPT


def stack_of(files: list[str], manifest: str) -> Stack:
    """Run module 1.2's detection over a repository root fetched from the API.

    Only the two things detect_stack reads are written out: package.json, and
    an empty placeholder for whichever Vite config the root actually has. The
    decision itself is entirely detect_stack's.
    """
    with tempfile.TemporaryDirectory(prefix="prodpilot-detect-") as workspace:
        root = Path(workspace)
        (root / "package.json").write_text(manifest, encoding="utf-8")
        for name in VITE_CONFIGS:
            if name in files:
                (root / name).write_text("", encoding="utf-8")
        try:
            return detect_stack(root).stack
        except DetectionError as exc:
            logger.warning("detection failed on a fetched root: %s", exc)
            return Stack.UNRECOGNIZED


def examine(client: Client, repo: dict) -> Verdict:
    """Decide one candidate. Licence first, then ProdPilot's own detection."""
    name = repo.get("full_name")
    if not isinstance(name, str) or not name:
        return Verdict("", UNREADABLE, detail="the search result had no full name")

    allowed, key, why = permissive(repo)
    if not allowed:
        return Verdict(name, why, detail=f"license {key or 'absent'}")

    branch = repo.get("default_branch") or "main"
    try:
        files = client.root(name, branch)
        if "package.json" not in files:
            return Verdict(name, UNDETECTED, detail="no package.json at the root")
        manifest = client.text(name, "package.json", branch)
        commit = client.head(name, branch)
    except Unreachable:
        # Says nothing about this repository, so it gets no verdict. Letting it
        # propagate leaves the candidate undecided and a later run retries it.
        raise
    except NotFound as exc:
        return Verdict(name, UNREADABLE, detail=str(exc))
    except GhError as exc:
        return Verdict(name, UNREADABLE, detail=str(exc))

    stack = stack_of(files, manifest)
    if stack is Stack.UNRECOGNIZED:
        return Verdict(name, UNDETECTED, detail="detect_stack did not recognise it")

    entry = Entry(
        name=name,
        url=repo.get("clone_url") or f"https://github.com/{name}.git",
        commit=commit,
        license=key,
        stack=stack.value,
        stars=int(repo.get("stargazers_count") or 0),
    )
    return Verdict(name, KEPT, entry=entry)


# Search is capped at 1000 results per query, so the corpus is gathered from
# several queries that do not overlap on star count. Each names a dependency
# the two supported stacks actually declare, and each is tagged with the stack
# it looks for so that one stack cannot fill the whole dataset.
QUERIES = (
    (Stack.NODE_EXPRESS, "express in:name,description language:JavaScript stars:50..500"),
    (Stack.NODE_EXPRESS, "express in:name,description language:JavaScript stars:501..5000"),
    (Stack.NODE_EXPRESS, "express in:name,description language:TypeScript stars:50..2000"),
    (Stack.NODE_EXPRESS, "express in:name,description language:JavaScript stars:10..49"),
    (Stack.REACT_VITE, "vite react in:name,description language:JavaScript stars:50..500"),
    (Stack.REACT_VITE, "vite react in:name,description language:JavaScript stars:501..5000"),
    (Stack.REACT_VITE, "vite react in:name,description language:TypeScript stars:50..2000"),
    (Stack.REACT_VITE, "vite in:name,description language:TypeScript stars:20..2000"),
    (Stack.REACT_VITE, "react vite template in:name,description stars:5..2000"),
)


def collect(
    client: Client,
    store: Store,
    target: int = 500,
    queries: tuple[tuple[Stack, str], ...] = QUERIES,
) -> Counts:
    """Collect until the dataset holds target repositories, or the queries run out.

    The target is split evenly across the stacks the queries look for. Without
    that, whichever stack is searched first fills the dataset on its own, since
    Express repositories alone outnumber the target several times over, and a
    model trained on one stack would be no use on the other.

    Resumable. Everything already in the manifest is skipped, and the target
    counts entries already stored, so rerunning after an interruption continues
    from where it stopped.
    """
    seen = store.seen()
    counts = store.counts()
    stacks = {stack for stack, _ in queries}
    share = max(1, target // len(stacks)) if stacks else target
    logger.info("resuming with %s kept and %s already examined, %s per stack",
                counts.kept, len(seen), share)

    for stack, query in queries:
        if counts.kept >= target:
            break
        if counts.by_stack.get(stack.value, 0) >= share:
            logger.info("%s has its share already, skipping: %s", stack.value, query)
            continue
        logger.info("query: %s", query)
        try:
            for repo in client.search(query):
                if counts.kept >= target:
                    break
                if counts.by_stack.get(stack.value, 0) >= share:
                    break
                name = repo.get("full_name")
                if not name or name in seen:
                    continue
                seen.add(name)
                verdict = examine(client, repo)
                store.write(verdict)
                counts.add(verdict)
                if counts.candidates % 25 == 0:
                    logger.info("%s", counts.summary())
        except Unreachable as exc:
            # The network is gone, not this query. Stop cleanly rather than
            # racing through the remaining queries failing each one.
            logger.warning("collection stopped, the network is unreachable: %s", exc)
            break
        except GhError as exc:
            # A failed query must not lose the work already written.
            logger.warning("query stopped early: %s", exc)
            continue

    logger.info("%s", counts.summary())
    return counts

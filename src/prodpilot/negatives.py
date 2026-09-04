"""Synthetic negatives for the scoring model's training set.

Scope is Phase 5 module 5.1. Section 6 of the Complete Solution Document says
synthetic negatives are created by programmatically breaking working
repositories in controlled ways. This is that step, and nothing more: it labels
nothing by deployment outcome and trains nothing.

Each negative breaks exactly one rule from the frozen store, and the break is
the inverse of a fix modules 3.2 through 3.4 already produce. Removing the
non-root USER line from a Dockerfile undoes the SEC-001 template. Deleting the
.dockerignore undoes BLD-002. Hardcoding the port undoes ENV-002. Nothing here
invents a defect the ruleset does not describe, so every negative has a rule id
that 5.3 can use as ground truth.

Why every negative is verified
------------------------------
A break that does not actually break anything would poison the training set
with a mislabelled example, and it would do so silently. So each one is checked
against module 3.6's verifier twice: the rule must pass on the untouched copy,
and it must fail after the break.

A break that pushes a second rule into failing is discarded too. Section 6 asks
for one violation at a time, so an edit that also breaks a neighbouring rule
makes the label wrong, and a full audit before and after is the only way to see
it. A rule that merely stops being evaluable is not a second violation, so that
is recorded rather than rejected.

Five rules cannot be violated in isolation at all. Deleting the file a rule
requires also fails every other rule that inspects that file, so removing a
Dockerfile fails the multi stage and non-root rules with it. Those breaks are
declared and then rejected by this check, which is the honest outcome: the
ruleset does not admit a single violation there.

Both checks tie this to the frozen store rather than to a guess about what a
violation looks like. The rules' own checkers decide.
"""

from __future__ import annotations

import json
import logging
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from prodpilot import audit
from prodpilot.findings import Status
from prodpilot.rules import get_rule
from prodpilot.verify import check

logger = logging.getLogger(__name__)

# A repository fetched from GitHub brings its history with it. A negative is a
# working tree, not a history, so the copy leaves .git behind: it halves the
# copy, and it avoids the read-only object files that make a plain delete fail
# on Windows.
SKIP = shutil.ignore_patterns(".git")


def wipe(path: Path) -> None:
    """Delete a tree, clearing the read-only bit git sets on its objects."""

    def retry(action, name, _exc):
        Path(name).chmod(stat.S_IWRITE)
        action(name)

    shutil.rmtree(path, onerror=retry)


class BreakError(Exception):
    """Raised when a break is declared against a rule that does not exist."""


# --------------------------------------------------------------------------
# edits, each one small enough that what it changes is obvious
# --------------------------------------------------------------------------


def drop(*names: str) -> Callable[[Path], bool]:
    """Delete a file or directory, whichever of the names is present."""

    def edit(root: Path) -> bool:
        for name in names:
            target = root / name
            if target.is_dir():
                wipe(target)
                return True
            if target.is_file():
                target.unlink()
                return True
        return False

    return edit


def drop_line(name: str, needle: str) -> Callable[[Path], bool]:
    """Remove every line of a file that is exactly needle."""

    def edit(root: Path) -> bool:
        target = root / name
        if not target.is_file():
            return False
        lines = target.read_text(encoding="utf-8").splitlines()
        kept = [line for line in lines if line.strip() != needle]
        if len(kept) == len(lines):
            return False
        target.write_text("\n".join(kept) + "\n", encoding="utf-8")
        return True

    return edit


def drop_key(name: str, *path: str) -> Callable[[Path], bool]:
    """Remove one key from a JSON file, addressed by its path."""

    def edit(root: Path) -> bool:
        target = root / name
        if not target.is_file():
            return False
        try:
            document = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        holder = document
        for step in path[:-1]:
            holder = holder.get(step) if isinstance(holder, dict) else None
            if not isinstance(holder, dict):
                return False
        if not isinstance(holder, dict) or path[-1] not in holder:
            return False
        del holder[path[-1]]
        target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        return True

    return edit


def sub(name: str, old: str, new: str) -> Callable[[Path], bool]:
    """Replace text in a file, doing nothing when the text is not there."""

    def edit(root: Path) -> bool:
        target = root / name
        if not target.is_file():
            return False
        text = target.read_text(encoding="utf-8")
        if old not in text:
            return False
        target.write_text(text.replace(old, new), encoding="utf-8")
        return True

    return edit


def add(name: str, body: str) -> Callable[[Path], bool]:
    """Write a file, creating the directories it needs."""

    def edit(root: Path) -> bool:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return True

    return edit


# A credential shaped string, used to break the secret scanning rules. It is
# not a real key and matches no live provider account.
LEAK = 'const key = "sk_live_9fK2mQ7bZx4LpW1nR8tYv3JhC6dGe0sUiOaXcVbN";\n'

# A working single stage Dockerfile. Replacing the two stage one breaks BLD-006
# while leaving the unprivileged user and the start command in place, so the
# other Dockerfile rules are untouched.
SINGLE_STAGE = """FROM node:20-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
USER node
EXPOSE 3000
CMD ["node", "src/server.js"]
"""


@dataclass(frozen=True)
class Break:
    """One controlled way to violate one rule."""

    rule_id: str
    note: str
    edit: Callable[[Path], bool]

    def __post_init__(self) -> None:
        if get_rule(self.rule_id) is None:
            raise BreakError(f"{self.rule_id} is not a rule in the store")


BREAKS: tuple[Break, ...] = (
    # Node with Express
    Break("BLD-001", "delete the Dockerfile", drop("Dockerfile")),
    Break("BLD-002", "delete the .dockerignore", drop(".dockerignore")),
    Break("BLD-003", "delete the CI workflow", drop(".github")),
    Break("BLD-004", "remove the start script", drop_key("package.json", "scripts", "start")),
    Break("BLD-005", "remove the engines range", drop_key("package.json", "engines")),
    Break("BLD-006", "collapse the build to a single stage",
          add("Dockerfile", SINGLE_STAGE)),
    Break("SEC-001", "run the container as root", drop_line("Dockerfile", "USER node")),
    Break("SEC-002", "remove the helmet registration",
          drop_line("src/server.js", "app.use(helmet());")),
    Break("ENV-002", "hardcode the port",
          sub("src/server.js", "app.listen(process.env.PORT)", "app.listen(3000)")),
    Break("OBS-001", "rename the health endpoint so no route serves it",
          sub("src/server.js", '"/health"', '"/version"')),
    Break("GIT-001", "delete the .gitignore", drop(".gitignore")),
    Break("GIT-002", "stop ignoring node_modules", drop_line(".gitignore", "node_modules")),
    Break("SCR-001", "stop ignoring the env file", drop_line(".gitignore", ".env")),
    Break("SCR-002", "commit a credential", add("src/keys.js", LEAK)),
    # React with Vite
    Break("BLD-007", "delete the Dockerfile", drop("Dockerfile")),
    Break("BLD-008", "delete the .dockerignore", drop(".dockerignore")),
    Break("BLD-009", "delete the nginx config", drop("nginx.conf")),
    Break("BLD-010", "delete the CI workflow", drop(".github")),
    Break("BLD-011", "remove the build script", drop_key("package.json", "scripts", "build")),
    Break("BLD-013", "remove the single page fallback",
          drop_line("nginx.conf", "try_files $uri $uri/ /index.html;")),
    Break("SEC-005", "run the container as root", drop_line("Dockerfile", "USER nginx")),
    Break("SEC-006", "remove the content security policy",
          drop_line("nginx.conf",
                    "add_header Content-Security-Policy \"default-src 'self'\" always;")),
    Break("OBS-005", "remove the health location",
          sub("nginx.conf", "location /health {", "location /status {")),
    Break("GIT-004", "delete the .gitignore", drop(".gitignore")),
    Break("GIT-005", "stop ignoring node_modules", drop_line(".gitignore", "node_modules")),
    Break("GIT-006", "stop ignoring the build output", drop_line(".gitignore", "dist")),
    Break("SCR-003", "stop ignoring the env file", drop_line(".gitignore", ".env")),
    Break("SCR-004", "commit a credential", add("src/keys.js", LEAK)),
)


@dataclass(frozen=True)
class Negative:
    """One broken copy of a real project, with the rule it violates."""

    name: str
    source: str
    rule_id: str
    note: str
    stack: str
    path: str
    # Any other rule whose status moved but did not start failing, so the
    # record is complete even though only rule_id is the violation.
    also: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "source": self.source,
            "rule_id": self.rule_id,
            "note": self.note,
            "stack": self.stack,
            "path": self.path,
            "also": list(self.also),
        }


def statuses(root: Path) -> dict[str, Status]:
    """Every rule's status for a project, from one full audit."""
    return {r.rule_id: r.status for r in audit.run(root).results}


def make(source: str | Path, out: str | Path, brk: Break) -> Negative | None:
    """Break one rule in a copy of a project, keeping it only if it worked.

    Returns None when the rule did not pass to begin with, when the edit found
    nothing to change, when the rule still passes afterwards, or when the edit
    moved a second rule as well. Each of those produces a mislabelled example,
    so none of them are recorded.
    """
    src = Path(source)
    target = Path(out)

    if check(src, brk.rule_id).status is not Status.PASS:
        logger.info("%s does not pass %s, so it cannot be broken", src.name, brk.rule_id)
        return None

    if target.exists():
        wipe(target)
    shutil.copytree(src, target, ignore=SKIP)
    before = statuses(target)

    if not brk.edit(target):
        logger.info("%s found nothing to change in %s", brk.rule_id, src.name)
        wipe(target)
        return None

    after = statuses(target)
    if after.get(brk.rule_id) is not Status.FAIL:
        logger.info("%s reports %s after the break, not fail",
                    brk.rule_id, after.get(brk.rule_id))
        wipe(target)
        return None

    broke = sorted(r for r in before
                   if r != brk.rule_id
                   and after.get(r) is Status.FAIL
                   and before[r] is not Status.FAIL)
    if broke:
        logger.info("%s also broke %s, so one violation cannot be labelled here",
                    brk.rule_id, ", ".join(broke))
        wipe(target)
        return None

    also = tuple(f"{r}: {before[r].value} to {after[r].value}" for r in sorted(before)
                 if r != brk.rule_id and before[r] is not after.get(r))

    return Negative(
        name=target.name,
        source=src.name,
        rule_id=brk.rule_id,
        note=brk.note,
        stack=get_rule(brk.rule_id).stack.value,
        path=str(target),
        also=also,
    )


def build(sources: dict[str, str | Path], out: str | Path,
          breaks: tuple[Break, ...] = BREAKS) -> list[Negative]:
    """Make one negative per break, from whichever source project suits it.

    sources maps a stack value to the working project to break. A break whose
    rule belongs to the other stack is skipped, and so is one that cannot be
    verified, so what comes back is only negatives with a true label.
    """
    root = Path(out)
    root.mkdir(parents=True, exist_ok=True)

    made: list[Negative] = []
    for brk in breaks:
        stack = get_rule(brk.rule_id).stack.value
        source = sources.get(stack)
        if source is None:
            continue
        negative = make(source, root / f"{Path(source).name}_{brk.rule_id.lower()}", brk)
        if negative is not None:
            made.append(negative)

    logger.info("built %s of %s negatives", len(made), len(breaks))
    return made


def write(negatives: list[Negative], path: str | Path) -> None:
    """Record the negatives, one per line, with the rule each one violates."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for negative in negatives:
            handle.write(json.dumps(negative.to_dict(), ensure_ascii=False) + "\n")

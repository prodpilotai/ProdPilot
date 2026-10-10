"""One view of an audit, shared by every report format.

The SARIF, Markdown and HTML reports all render the same View, so a finding is
worded, located and fingerprinted once and the three formats cannot disagree
about it. Building a view runs the audit and reads the project; it writes
nothing.

Where a finding points
----------------------
Paths are relative to the root of the Git repository holding the project, so a
report of a project in a subdirectory still points at the right file when it is
uploaded for the whole repository. Outside a Git repository they are relative
to the project itself.

A finding the checker tied to a file and a line points there; the checkers know
the line but not the column, so no column is reported. A file-existence rule
whose file is missing cannot point at the file it asks for, and a rule about the
project as a whole has no file at all. Both point at package.json, the file that
defines the project, and the message names the missing file.

Secrets
-------
A credential is never printed, in any format. The secret checkers' own messages
show part of the value, so for those rules the message is rebuilt from the
scanner's hits as file, line and a preview of the first four characters with the
length. Every message then passes through redact, which removes any full or
partially masked value the scanner found, as a second line of defence.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from prodpilot import audit
from prodpilot.audit import Report, RuleResult
from prodpilot.blueprint import Priority
from prodpilot.entropy import HistoryUnavailable, Hit, scan_history, scan_project
from prodpilot.rules import CheckType

logger = logging.getLogger(__name__)

DOCS = "https://website.prodpilot-ai.workers.dev/docs/getting-started/introduction/"
RULES_DOCS = "https://website.prodpilot-ai.workers.dev/docs/reference/rules/"

# P0 and P1 are errors, P2 and P3 warnings, P4 and P5 notes.
LEVELS: dict[Priority, str] = {
    Priority.P0: "error", Priority.P1: "error",
    Priority.P2: "warning", Priority.P3: "warning",
    Priority.P4: "note", Priority.P5: "note",
}

FIX_HELP = {
    "STATIC": ("ProdPilot supplies the exact change, the same for every project. Ask your "
               "agent for it with prodpilot_fix_instruction and apply it exactly as given."),
    "DYNAMIC-PARAMETRIC": ("ProdPilot supplies the exact change, filled in with values read "
                           "from this project. When a value is ambiguous it refuses and "
                           "names the candidates instead of guessing."),
    "DYNAMIC-DELEGATED": ("ProdPilot gives your agent a constraint contract: the "
                          "requirement, the files it may change and what must stay "
                          "unchanged. The agent writes the change and the rule's own "
                          "checker decides whether it worked."),
}

# How many secret hits a message lists before it summarises the rest.
LISTED = 5


@dataclass(frozen=True)
class Location:
    """A file relative to the repository root, and a line when one is known."""

    uri: str
    line: int | None = None

    def label(self) -> str:
        return f"{self.uri}:{self.line}" if self.line else self.uri


@dataclass(frozen=True)
class Item:
    """One failing rule as every report shows it."""

    result: RuleResult
    message: str
    locations: tuple[Location, ...]
    # True when the rule names no file of its own, so the location is the
    # project's package.json rather than the place of the problem.
    attached: bool = False

    @property
    def rule_id(self) -> str:
        return self.result.rule_id

    @property
    def level(self) -> str:
        return LEVELS[self.result.priority]

    @property
    def fingerprint(self) -> str:
        """Stable across commits: the rule and its primary file, never a line number."""
        first = self.locations[0].uri if self.locations else ""
        return hashlib.sha256(f"{self.rule_id}\0{first}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class View:
    """An audited project, ready to render."""

    root: Path
    base: Path
    report: Report
    items: tuple[Item, ...]
    notices: tuple[str, ...] = field(default_factory=tuple)

    @property
    def project(self) -> str:
        return self.report.project

    def failing_by_priority(self) -> dict[str, int]:
        return self.report.by_priority()

    def failing_by_domain(self) -> dict[str, int]:
        return self.report.by_domain()


def mask(value: str) -> str:
    """The first four characters and the length, never more."""
    return f"{value[:4]}... ({len(value)} characters)"


def redact(text: str, secrets: tuple[str, ...]) -> str:
    """Remove every found value, and every partial form a checker printed, from text."""
    for value in sorted(secrets, key=len, reverse=True):
        if len(value) <= 12:
            partial = value[:2] + "..." + value[-2:]
        else:
            partial = value[:6] + "..." + value[-4:]
        text = text.replace(value, mask(value)).replace(partial, mask(value))
    return text


def repository_root(root: Path) -> Path:
    """The Git repository holding root, or root itself when there is none."""
    try:
        done = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, timeout=30, check=False)
    except FileNotFoundError:
        logger.info("git is not installed, so report paths are relative to %s", root)
        return root
    if done.returncode != 0 or not done.stdout.strip():
        return root
    return Path(done.stdout.strip()).resolve()


def relative(path: Path, base: Path) -> str:
    return path.resolve().relative_to(base).as_posix()


def anchor(root: Path, base: Path) -> str:
    """The file a project-level finding is attached to."""
    return relative(root / "package.json", base)


def secret_item(result: RuleResult, root: Path, base: Path,
                hits: list[Hit]) -> Item:
    if not hits:
        # The rule failed without hits: the scan itself could not run, and the
        # checker's own message says why without carrying any value.
        return Item(result, result.detail, (Location(anchor(root, base)),), attached=True)
    shown = [f"{h.file}:{h.line} {mask(h.value)}" if h.line else f"{h.file} {mask(h.value)}"
             for h in hits[:LISTED]]
    more = f", and {len(hits) - LISTED} more" if len(hits) > LISTED else ""
    where = "in committed history" if result.rule_id.startswith("GIT") else "in source"
    message = (f"{len(hits)} credential-shaped string(s) {where}: "
               f"{'; '.join(shown)}{more}")
    seen: list[Location] = []
    for h in hits:
        here = Location(relative(root / h.file, base), h.line or None)
        if here not in seen:
            seen.append(here)
    return Item(result, message, tuple(seen))


def plain_item(result: RuleResult, root: Path, base: Path) -> Item:
    locations: list[Location] = []
    attached = False
    missing: list[str] = []
    for found in result.where:
        target = root / found.file
        if found.file and target.is_file():
            here = Location(relative(target, base), found.line)
        else:
            if found.file and found.file != root.name:
                missing.append(found.file)
            here = Location(anchor(root, base))
            attached = True
        if here not in locations:
            locations.append(here)
    if not locations:
        locations.append(Location(anchor(root, base)))
        attached = True
    message = result.detail
    if missing:
        message += f" (expected file: {', '.join(sorted(set(missing)))})"
    return Item(result, message, tuple(locations), attached)


def build(root: str | Path) -> View:
    """Audit a project and describe every failing rule. Writes nothing.

    Raises NotADirectoryError when root is not a project directory.
    """
    base_dir = Path(root).resolve()
    report = audit.run(base_dir)
    repo = repository_root(base_dir)

    current: list[Hit] | None = None
    history: list[Hit] | None = None
    items: list[Item] = []
    secrets: set[str] = set()
    for result in report.issues:
        if result.rule.check_type is CheckType.ENTROPY_SCAN:
            if result.rule_id.startswith("GIT"):
                if history is None:
                    try:
                        history = scan_history(base_dir)
                    except HistoryUnavailable as exc:
                        logger.warning("history of %s could not be scanned: %s", base_dir, exc)
                        history = []
                hits = history
            else:
                if current is None:
                    current = scan_project(base_dir)
                hits = current
            secrets.update(h.value for h in hits)
            items.append(secret_item(result, base_dir, repo, hits))
        else:
            items.append(plain_item(result, base_dir, repo))

    # The working tree is always scanned once, so a value that reached any other
    # rule's message is removed as well.
    if current is None:
        current = scan_project(base_dir)
    secrets.update(h.value for h in current)
    hidden = tuple(secrets)
    items = [Item(i.result, redact(i.message, hidden), i.locations, i.attached) for i in items]
    notices = tuple(redact(n, hidden) for n in report.notices)
    return View(root=base_dir, base=repo, report=report, items=tuple(items), notices=notices)

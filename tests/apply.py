"""An executor for the Section 5.2 first form contract, for the loop run.

In production the IDE agent applies the change. No agent can run inside an
automated test, so this executes the contract literally instead: create the
file, or place the content at the named anchor, exactly as the instruction says
and with nothing else touched.

This is not a stand in for verification. Verification in that run is the real
verify.check, over the real files this writes. What this replaces is only the
hand that edits the file, and for a content contract that hand has no judgement
to exercise: the instruction fully determines the change, so an agent applying
it correctly would produce what this produces.

It follows the whole contract, including its constraint. Content already in
place is left alone, so a retry never duplicates it, and any package the
contract lists that package.json does not declare is added to its dependencies.
An anchor that names a statement covers the whole statement, however many lines
it spans, so nothing is inserted into the middle of one.

It cannot author a constraint contract, because that form deliberately carries
no content. A delegated rule therefore comes back unapplied, which is honest
rather than convenient: the loop then retries it, exhausts the budget, and
records it for manual review.

Anchors it cannot place are refused the same way. A refusal here leaves the rule
failing, which the verifier then reports on its own.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from prodpilot.dispatch import Claim, Fix, Form
from prodpilot.templates import LINE, Action

APP = re.compile(r"\b(?:const|let|var)\s+(\w+)\s*=\s*express\s*\(\s*\)")
ROUTE = re.compile(r"^\s*\w+\.(?:get|post|put|patch|delete|use|all)\s*\(")
PATHED = re.compile(r"""^\s*\w+\.(?:get|post|put|patch|delete|head|options|all)\s*\(\s*["'`]/""")
LISTEN = re.compile(r"^\s*(?:(?:const|let|var)\s+\w+\s*=\s*)?\w+\.listen\s*\(")
CORS = re.compile(r"^\s*\w+\.use\s*\(.*\bcors\b")
CMD = re.compile(r"^\s*(?:CMD|ENTRYPOINT)\b")


def end_of(lines: list[str], start: int) -> int:
    """The last line of the statement that begins on start.

    Counts brackets outside strings and line comments until they balance, which
    is enough for the statements these anchors name.
    """
    depth = 0
    quote = None
    for i in range(start, len(lines)):
        line = lines[i]
        j = 0
        while j < len(line):
            ch = line[j]
            if quote:
                if ch == "\\":
                    j += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch in "\"'`":
                quote = ch
            elif line.startswith("//", j):
                break
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            j += 1
        if quote != "`":
            quote = None
        if depth <= 0:
            return i
    return start


def first(lines: list[str], pattern: re.Pattern, start: int = 0) -> int | None:
    return next((i for i in range(start, len(lines)) if pattern.search(lines[i])), None)


def present(text: str, content: str) -> bool:
    """Whether content already sits in the file, line for line."""
    want = [line.strip() for line in content.splitlines() if line.strip()]
    have = [line.strip() for line in text.splitlines()]
    size = len(want)
    return not want or any(have[i:i + size] == want for i in range(len(have) - size + 1))


def insertion(lines: list[str], anchor: str) -> int | None:
    """The line index content goes in front of, or None when it cannot be found."""
    if anchor == "package:root":
        at = next((i for i, line in enumerate(lines) if line.strip().startswith("{")), None)
        return None if at is None else at + 1
    if anchor == "docker:before-cmd":
        return first(lines, CMD)
    if anchor == "express:before-routes":
        app = first(lines, APP)
        if app is None:
            return None
        route = first(lines, PATHED, app + 1)
        return route if route is not None else end_of(lines, app) + 1
    if anchor == "express:after-routes":
        hits = [i for i, line in enumerate(lines) if ROUTE.match(line)]
        return end_of(lines, hits[-1]) + 1 if hits else None
    if anchor == "express:after-listen":
        at = first(lines, LISTEN)
        return None if at is None else end_of(lines, at) + 1
    return None


def place(text: str, anchor: str, content: str) -> str | None:
    """Insert content at a named anchor, or return None when it cannot be found."""
    if present(text, content):
        return text
    body = content.rstrip("\n")
    if anchor == "file:end":
        return text.rstrip("\n") + "\n" + body + "\n"
    lines = text.splitlines()
    at = insertion(lines, anchor)
    if at is None:
        return None
    lines.insert(at, body)
    return "\n".join(lines) + "\n"


def replace(text: str, anchor: str, content: str) -> str | None:
    """Replace the block an anchor names with new content."""
    if present(text, content):
        return text
    body = content.rstrip("\n")
    if anchor == "file:end":
        return text.rstrip("\n") + "\n" + body + "\n"
    lines = text.splitlines()
    if anchor.startswith(LINE):
        number = anchor[len(LINE):]
        if not number.isdigit() or not 0 < int(number) <= len(lines):
            return None
        lines[int(number) - 1] = body
    elif anchor in ("express:listen-call", "express:cors-call"):
        at = first(lines, LISTEN if anchor == "express:listen-call" else CORS)
        if at is None:
            if anchor == "express:cors-call":
                return place(text, "express:before-routes", content)
            return None
        lines[at:end_of(lines, at) + 1] = [body]
    else:
        return None
    return "\n".join(lines) + "\n"


def declare(root: Path, packages: dict[str, str]) -> list[str]:
    """Add each listed package the manifest's dependencies do not name yet."""
    path = root / "package.json"
    if not packages or not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    deps = data.setdefault("dependencies", {})
    added = [name for name in packages if name not in deps]
    for name in added:
        deps[name] = packages[name]
    if added:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return added


def write(root: Path, fix: Fix) -> str:
    """Carry out one content contract. Returns what was done, or why it was not."""
    body = fix.body
    target = root / body["file_path"]
    action = body["action"]

    if action == Action.CREATE_FILE.value:
        if target.is_file() and target.read_text(encoding="utf-8") == body["content"]:
            done = f"{body['file_path']} already holds this content"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body["content"], encoding="utf-8")
            done = f"created {body['file_path']}"
    else:
        if not target.is_file():
            return f"no file at {body['file_path']}"
        text = target.read_text(encoding="utf-8")
        if action == Action.INSERT_AFTER.value:
            updated = place(text, body["anchor"], body["content"])
        elif action == Action.REPLACE_BLOCK.value:
            updated = replace(text, body["anchor"], body["content"])
        else:
            return f"unknown action {action}"
        if updated is None:
            return f"could not locate the anchor {body['anchor']} in {body['file_path']}"
        if updated == text:
            done = f"the content is already in place in {body['file_path']}"
        else:
            target.write_text(updated, encoding="utf-8")
            done = f"{action} at {body['anchor']} in {body['file_path']}"

    added = declare(root, body.get("packages") or {})
    if added:
        done += f", and declared {', '.join(added)} in package.json"
    return done


def applier(root: str | Path):
    """Build the agent seam for one project."""
    base = Path(root)

    def agent(fix: Fix) -> Claim:
        if fix.form is Form.CONSTRAINT:
            return Claim(fix.rule_id, False, "a constraint contract needs an author")
        try:
            done = write(base, fix)
        except (OSError, ValueError) as exc:
            return Claim(fix.rule_id, False, f"writing failed: {exc}")
        applied = not done.startswith(("could not", "no file", "unknown"))
        return Claim(fix.rule_id, applied, done)

    return agent

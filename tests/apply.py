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

It cannot author a constraint contract, because that form deliberately carries
no content. A delegated rule therefore comes back unapplied, which is honest
rather than convenient: the loop then retries it, exhausts the budget, and
records it for manual review.

Anchors it cannot place are refused the same way. A refusal here leaves the rule
failing, which the verifier then reports on its own.
"""

from __future__ import annotations

import re
from pathlib import Path

from prodpilot.dispatch import Claim, Fix, Form
from prodpilot.templates import Action

APP = re.compile(r"\b(?:const|let|var)\s+(\w+)\s*=\s*express\s*\(\s*\)")
ROUTE = re.compile(r"^\s*\w+\.(?:get|post|put|patch|delete|use|all)\s*\(")
LISTEN = re.compile(r"^\s*\w+\.listen\s*\(")
CMD = re.compile(r"^\s*(?:CMD|ENTRYPOINT)\b")


def place(text: str, anchor: str, content: str) -> str | None:
    """Insert content at a named anchor, or return None when it cannot be found."""
    body = content.rstrip("\n")
    lines = text.splitlines()

    if anchor == "file:end":
        return text.rstrip("\n") + "\n" + body + "\n"

    at = anchor_line(lines, anchor)
    if at is None:
        return None
    if anchor == "docker:before-cmd":
        lines.insert(at, body)
    else:
        lines.insert(at + 1, body)
    return "\n".join(lines) + "\n"


def anchor_line(lines: list[str], anchor: str) -> int | None:
    """The line index an anchor names, counted from zero."""
    if anchor == "package:root":
        return next((i for i, line in enumerate(lines) if line.strip().startswith("{")), None)
    if anchor == "docker:before-cmd":
        return next((i for i, line in enumerate(lines) if CMD.match(line)), None)
    if anchor == "express:before-routes":
        return next((i for i, line in enumerate(lines) if APP.search(line)), None)
    if anchor == "express:after-routes":
        hits = [i for i, line in enumerate(lines) if ROUTE.match(line)]
        return hits[-1] if hits else None
    if anchor in ("express:after-listen", "express:listen-call"):
        return next((i for i, line in enumerate(lines) if LISTEN.match(line)), None)
    return None


def replace(text: str, anchor: str, content: str) -> str | None:
    """Replace the block an anchor names with new content."""
    if anchor == "file:end":
        return text.rstrip("\n") + "\n" + content.rstrip("\n") + "\n"
    lines = text.splitlines()
    at = anchor_line(lines, anchor)
    if at is None:
        return None
    lines[at] = content.rstrip("\n")
    return "\n".join(lines) + "\n"


def write(root: Path, fix: Fix) -> str:
    """Carry out one content contract. Returns what was done, or why it was not."""
    body = fix.body
    target = root / body["file_path"]
    action = body["action"]

    if action == Action.CREATE_FILE.value:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body["content"], encoding="utf-8")
        return f"created {body['file_path']}"

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
    target.write_text(updated, encoding="utf-8")
    return f"{action} at {body['anchor']} in {body['file_path']}"


def applier(root: str | Path):
    """Build the agent seam for one project."""
    base = Path(root)

    def agent(fix: Fix) -> Claim:
        if fix.form is Form.CONSTRAINT:
            return Claim(fix.rule_id, False, "a constraint contract needs an author")
        try:
            done = write(base, fix)
        except OSError as exc:
            return Claim(fix.rule_id, False, f"writing failed: {exc}")
        applied = not done.startswith(("could not", "no file", "unknown"))
        return Claim(fix.rule_id, applied, done)

    return agent

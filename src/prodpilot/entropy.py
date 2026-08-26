"""Entropy scanning for hardcoded secrets.

Scope is Phase 2 module 2.3. This module finds strings that look like
credentials. It reports them and never edits anything.

Method
------
Two signals, because neither alone is good enough.

Shannon entropy over the base64 and hex character sets, measured on runs of at
least 20 characters. This is the approach truffleHog established and it catches
keys nobody has a pattern for. On its own it is noisy, since minified code,
content hashes and long identifiers all score highly.

Known prefixes for the providers this project actually touches, GitHub and
Render among them. A string carrying one of these is a secret regardless of what
its entropy measures, and these carry no false positives worth speaking of.

The usual third signal, calling the provider to see whether a key is live, is
not available here. Section 2 of the Complete Solution Document states that no
external API is called at any point, so verification has to come from the two
static signals plus the context a name gives.

Failing closed
--------------
A candidate that scores near a threshold rather than clearly past it is still
reported, marked as borderline. Section 4.1 requires ambiguous cases to be
surfaced rather than guessed, and silently dropping a borderline string would be
guessing that it is safe. A reviewer can dismiss a false positive. Nobody can
dismiss a secret that was never shown to them.
"""

from __future__ import annotations

import logging
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

BASE64_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
HEX_CHARS = set("0123456789abcdefABCDEF")

# Thresholds follow the detect-secrets defaults, which sit between the noisy and
# the permissive ends of its documented range.
BASE64_LIMIT = 4.5
HEX_LIMIT = 3.0
MIN_LEN = 20

# How far below a limit still counts as borderline and gets reported anyway.
MARGIN = 0.35

# Prefixes that identify a credential on sight. Kept to providers in scope plus
# the few that turn up constantly in vibe-coded projects.
PREFIXES = (
    ("ghp_", "GitHub personal access token"),
    ("gho_", "GitHub OAuth token"),
    ("ghs_", "GitHub server token"),
    ("github_pat_", "GitHub fine-grained token"),
    ("rnd_", "Render API key"),
    ("sk-", "OpenAI style secret key"),
    ("sk_live_", "Stripe live secret key"),
    ("sk_test_", "Stripe test secret key"),
    ("AKIA", "AWS access key id"),
    ("ASIA", "AWS temporary access key id"),
    ("xoxb-", "Slack bot token"),
    ("xoxp-", "Slack user token"),
    ("AIza", "Google API key"),
    ("mongodb+srv://", "MongoDB connection string"),
    ("postgres://", "Postgres connection string"),
    ("mysql://", "MySQL connection string"),
)

# A name that says the value beside it is a credential.
SECRET_NAMES = re.compile(
    r"(SECRET|TOKEN|PASSWORD|PASSWD|API[_-]?KEY|APIKEY|PRIVATE[_-]?KEY|"
    r"ACCESS[_-]?KEY|AUTH|CREDENTIAL|CONN(ECTION)?[_-]?STRING)",
    re.IGNORECASE,
)

# Values that exist to be replaced. Flagging these trains people to ignore the
# scanner, which is worse than missing them.
PLACEHOLDERS = re.compile(
    r"^(your[_-]?|my[_-]?|some[_-]?|the[_-]?)?"
    r"(api[_-]?key|secret|token|password|value|key|here|xxx+|placeholder|"
    r"changeme|todo|example|sample|dummy|test|fake|redacted|removed)"
    r"([_-]?(here|goes[_-]?here|value|placeholder))?$",
    re.IGNORECASE,
)

SKIP_DIRS = frozenset({"node_modules", "dist", "build", ".git", "coverage", ".next", "vendor"})
SKIP_FILES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json",
    "composer.lock", "poetry.lock",
})
SCAN_SUFFIXES = frozenset({
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".json", ".yml", ".yaml",
    ".env", ".sh", ".toml", ".ini", ".cfg", ".conf",
})

# Runs of characters worth measuring, quoted strings and bare tokens alike.
RUN_RE = re.compile(r"[A-Za-z0-9+/=_\-\.:]{%d,}" % MIN_LEN)


@dataclass(frozen=True)
class Hit:
    """One string that looks like a credential."""

    file: str
    line: int
    value: str
    reason: str
    entropy: float = 0.0
    borderline: bool = False

    @property
    def masked(self) -> str:
        """The value with its middle removed.

        A finding has to be actionable without reprinting the secret in a
        report, a log, or a terminal that may be shared.
        """
        v = self.value
        if len(v) <= 12:
            return v[:2] + "..." + v[-2:]
        return v[:6] + "..." + v[-4:]

    def to_dict(self) -> dict[str, object]:
        return {
            "file": self.file,
            "line": self.line,
            "value": self.masked,
            "reason": self.reason,
            "entropy": round(self.entropy, 2),
            "borderline": self.borderline,
        }


def shannon(text: str, charset: set[str]) -> float:
    """Shannon entropy of a string over one character set, in bits."""
    if not text:
        return 0.0
    total = 0.0
    for ch in charset:
        freq = float(text.count(ch)) / len(text)
        if freq > 0:
            total += -freq * math.log(freq, 2)
    return total


def is_placeholder(value: str) -> bool:
    """Whether a value is obviously a stand-in rather than a real credential."""
    stripped = value.strip("\"'`").strip()
    if PLACEHOLDERS.match(stripped):
        return True
    body = re.sub(r"[^A-Za-z0-9]", "", stripped)
    if not body:
        return True
    # A single repeated character, for example xxxxxxxx or 00000000.
    if len(set(body.lower())) <= 2:
        return True
    if stripped.startswith("<") and stripped.endswith(">"):
        return True
    return False


def prefix_hit(value: str) -> str | None:
    """The provider a value's prefix identifies, when one matches."""
    for prefix, label in PREFIXES:
        if value.startswith(prefix) and len(value) > len(prefix) + 6:
            return label
    return None


def scan_text(text: str, path: str) -> list[Hit]:
    """Find credential-looking strings in one blob of text."""
    hits: list[Hit] = []
    seen: set[tuple[int, str]] = set()

    for num, line in enumerate(text.splitlines(), start=1):
        named = bool(SECRET_NAMES.search(line))
        for match in RUN_RE.finditer(line):
            value = match.group(0)
            if (num, value) in seen:
                continue
            if is_placeholder(value):
                continue

            label = prefix_hit(value)
            if label:
                seen.add((num, value))
                hits.append(Hit(path, num, value, f"matches a known {label}", 0.0, False))
                continue

            b64 = shannon(value, BASE64_CHARS) if set(value) & BASE64_CHARS else 0.0
            hex_e = shannon(value, HEX_CHARS) if set(value) <= HEX_CHARS else 0.0

            over_b64 = b64 >= BASE64_LIMIT
            over_hex = hex_e >= HEX_LIMIT
            near_b64 = BASE64_LIMIT - MARGIN <= b64 < BASE64_LIMIT
            near_hex = HEX_LIMIT - MARGIN <= hex_e < HEX_LIMIT
            score = max(b64, hex_e)

            if over_b64 or over_hex:
                seen.add((num, value))
                why = "high entropy string"
                if named:
                    why += " assigned to a credential-shaped name"
                hits.append(Hit(path, num, value, why, score, False))
            elif named and (near_b64 or near_hex):
                # Reported rather than dropped. Section 4.1 requires an
                # ambiguous case to be surfaced instead of guessed.
                seen.add((num, value))
                hits.append(Hit(
                    path, num, value,
                    "borderline entropy next to a credential-shaped name",
                    score, True,
                ))
    return hits


def scan_file(path: str | Path, root: str | Path | None = None) -> list[Hit]:
    """Scan one file. An unreadable file yields nothing rather than raising."""
    p = Path(path)
    name = str(p)
    if root:
        try:
            name = str(p.relative_to(Path(root)).as_posix())
        except ValueError:
            pass
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.info("skipping %s during entropy scan: %s", p, exc)
        return []
    return scan_text(text, name)


def scan_files(root: str | Path) -> list[Path]:
    """Files worth scanning for secrets."""
    base = Path(root)
    out: list[Path] = []
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.name in SKIP_FILES:
            continue
        if p.suffix in SCAN_SUFFIXES or p.name.startswith(".env"):
            out.append(p)
    return sorted(out)


def scan_project(root: str | Path) -> list[Hit]:
    """Scan a whole project's working tree."""
    base = Path(root)
    if not base.is_dir():
        raise NotADirectoryError(f"not a project directory: {base}")
    hits: list[Hit] = []
    for p in scan_files(base):
        hits.extend(scan_file(p, base))
    return hits


def is_repo(root: str | Path) -> bool:
    """Whether a path is inside a Git working tree."""
    return (Path(root) / ".git").exists()


def scan_history(root: str | Path, limit: int = 200) -> list[Hit]:
    """Scan committed history for secrets that were later removed.

    A secret deleted from the working tree is still in the history and still
    compromised, which is why this rule exists separately from the source scan.

    limit caps how many commits are examined so a large repository cannot stall
    an audit. Raises HistoryUnavailable when git cannot be run.
    """
    base = Path(root)
    if not is_repo(base):
        raise HistoryUnavailable(f"{base} is not a Git repository")

    try:
        proc = subprocess.run(
            ["git", "log", f"-{limit}", "-p", "--no-color", "--no-merges"],
            cwd=str(base),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise HistoryUnavailable("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise HistoryUnavailable("reading git history timed out") from exc

    if proc.returncode != 0:
        raise HistoryUnavailable(f"git log failed: {proc.stderr.strip()[:200]}")

    hits: list[Hit] = []
    commit = "unknown"
    for line in proc.stdout.splitlines():
        if line.startswith("commit "):
            commit = line.split()[1][:8]
            continue
        # Only added lines matter. A removal is the secret leaving the tree,
        # which is exactly the case this rule is about.
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for hit in scan_text(line[1:], f"history:{commit}"):
            hits.append(Hit(hit.file, 0, hit.value, hit.reason, hit.entropy, hit.borderline))
    return hits


class HistoryUnavailable(Exception):
    """Raised when committed history cannot be read."""

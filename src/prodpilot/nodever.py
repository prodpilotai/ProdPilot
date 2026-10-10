"""Which Node.js version a project declares, and which one its image builds on.

Where a project declares its version
------------------------------------
Render chooses a service's Node version from these places, highest first: the
NODE_VERSION environment variable, a .node-version file, an .nvmrc file, and
the engines.node field of package.json (render.com/docs/node-version). ProdPilot
reads the same places in the same order, so the image it generates and the
service Render builds run the same major. NODE_VERSION is read from the
project's .env, because ProdPush seals that file and sends its values to Render
as environment values. A NODE_VERSION set by hand in the Render dashboard is not
visible from the project and is not read.

Which major the generated image uses
------------------------------------
A declaration that names one major uses that major when it is a supported LTS
line: 20, 22, 24 or 26, per the Node.js release schedule. One that names an
unsupported major, an end-of-life line such as 18 or an odd release, falls
back to the default, 24, Render's default for new services and the current
Active LTS line. A range that spans more than one major, or has no upper bound,
is ambiguous: the contract that needs the version refuses and lists the
supported majors the range allows, the same fail-closed behaviour every other
ambiguous extraction has. Render resolves an unbounded range to the newest
release, which changes over time, so guessing here would be wrong later.

What this does not change
-------------------------
The deployability model's build feature runs Render's build command in a local
Node image chosen by builds.requested and builds.major: .node-version, then
.nvmrc, then engines.node, with an unbounded range resolved to the newest local
image and anything unresolvable to Node 24. That computation is a model input
and stays exactly as 1.0.0 shipped it. This module is used only for generated
content and for informational notices.

Rule BLD-005 is part of the frozen 1.x ruleset and still asks package.json to
pin Node 20. The end-of-life notice says so rather than contradicting it.

End-of-life notices
-------------------
A project that declares an end-of-life major gets an informational notice in the
audit output and the reports. It is not a rule, it does not change the score,
and it does not block the gate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# From the Node.js release schedule, github.com/nodejs/Release schedule.json:
# the date each major reaches end of life.
END_OF_LIFE: dict[int, date] = {
    10: date(2021, 4, 30), 11: date(2019, 6, 1), 12: date(2022, 4, 30),
    13: date(2020, 6, 1), 14: date(2023, 4, 30), 15: date(2021, 6, 1),
    16: date(2023, 9, 11), 17: date(2022, 6, 1), 18: date(2025, 4, 30),
    19: date(2023, 6, 1), 20: date(2026, 4, 30), 21: date(2024, 6, 1),
    22: date(2027, 4, 30), 23: date(2025, 6, 1), 24: date(2028, 4, 30),
    25: date(2026, 6, 1), 26: date(2029, 4, 30), 27: date(2030, 4, 30),
}

# The LTS lines a generated image may use.
SUPPORTED = (20, 22, 24, 26)

# Render's default for new services, and the Active LTS line.
DEFAULT = 24

CODENAMES = {"argon": 4, "boron": 6, "carbon": 8, "dubnium": 10, "erbium": 12,
             "fermium": 14, "gallium": 16, "hydrogen": 18, "iron": 20, "jod": 22,
             "krypton": 24}

# Majors a range is resolved against. Wide enough to hold every line above.
UNIVERSE = range(4, 40)


@dataclass(frozen=True)
class Declaration:
    """One place a project states its Node version."""

    source: str
    spec: str

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "spec": self.spec}


@dataclass(frozen=True)
class Resolved:
    """What a declaration means for the generated image."""

    major: int | None
    candidates: tuple[int, ...] = ()
    reason: str = ""

    @property
    def ambiguous(self) -> bool:
        return self.major is None


def declarations(root: str | Path) -> tuple[Declaration, ...]:
    """Every Node version the project declares, highest precedence first."""
    base = Path(root)
    found: list[Declaration] = []

    env = base / ".env"
    if env.is_file():
        from prodpilot.sealing import pairs
        value = pairs(env.read_text(encoding="utf-8", errors="replace")).get("NODE_VERSION")
        if value and value.strip():
            found.append(Declaration(".env NODE_VERSION", value.strip()))

    for name in (".node-version", ".nvmrc"):
        path = base / name
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                found.append(Declaration(name, text.splitlines()[0].strip()))

    manifest = base / "package.json"
    if manifest.is_file():
        try:
            pkg = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            pkg = None
        engines = pkg.get("engines") if isinstance(pkg, dict) else None
        spec = engines.get("node") if isinstance(engines, dict) else None
        if isinstance(spec, str) and spec.strip():
            found.append(Declaration("package.json engines.node", spec.strip()))
    return tuple(found)


_PART = re.compile(r"(>=|<=|>|<|=|\^|~)?\s*v?(\d+)(?:\.(\d+|x|\*))?(?:\.(\d+|x|\*))?")


def majors(spec: str) -> tuple[set[int], bool] | None:
    """The majors a version spec allows, and whether it is bounded above.

    None when the spec cannot be read at all.
    """
    text = spec.strip().lower()
    if text in ("node", "latest", "current", "*", "x", "lts/*", "lts"):
        return {m for m in UNIVERSE}, False
    if text.startswith("lts/"):
        hit = CODENAMES.get(text.split("/", 1)[1])
        return ({hit}, True) if hit else None

    allowed: set[int] = set()
    bounded = True
    for alternative in text.split("||"):
        part = alternative.strip()
        if not part:
            return None
        hyphen = re.fullmatch(r"v?(\d+)\S*\s+-\s+v?(\d+)\S*", part)
        if hyphen:
            low, high = int(hyphen.group(1)), int(hyphen.group(2))
            allowed |= {m for m in UNIVERSE if low <= m <= high}
            continue
        terms = list(_PART.finditer(part))
        if not terms or "".join(t.group(0) for t in terms).replace(" ", "") != part.replace(" ", ""):
            return None
        low, high = min(UNIVERSE), None
        for term in terms:
            op, num = term.group(1) or "", int(term.group(2))
            minor, patch = term.group(3), term.group(4)
            # A bound with a non-zero minor or patch falls inside its major.
            precise = minor not in (None, "x", "*", "0") or patch not in (None, "x", "*", "0")
            if op in ("", "=", "^", "~"):
                low, high = max(low, num), num + 1 if high is None else min(high, num + 1)
            elif op == ">=":
                low = max(low, num)
            elif op == ">":
                low = max(low, num if precise else num + 1)
            elif op == "<":
                cap = num + 1 if precise else num
                high = cap if high is None else min(high, cap)
            elif op == "<=":
                high = num + 1 if high is None else min(high, num + 1)
        if high is None:
            bounded = False
            allowed |= {m for m in UNIVERSE if m >= low}
        else:
            allowed |= {m for m in UNIVERSE if low <= m < high}
    return allowed, bounded


def resolve(spec: str) -> Resolved:
    """Which major a declared spec gives the generated image, or why it cannot."""
    read = majors(spec)
    if read is None:
        return Resolved(None, SUPPORTED,
                        f"the declared Node version {spec!r} cannot be read")
    allowed, bounded = read
    fits = tuple(m for m in SUPPORTED if m in allowed)
    if not bounded or len(allowed) > 1:
        what = "has no upper bound" if not bounded else "spans more than one major"
        return Resolved(None, fits,
                        f"the declared Node version {spec!r} {what}, so which LTS line "
                        f"to build on is unclear")
    if not allowed:
        return Resolved(None, SUPPORTED,
                        f"the declared Node version {spec!r} allows no Node release")
    (only,) = allowed
    if only in SUPPORTED:
        return Resolved(only)
    return Resolved(DEFAULT, reason=f"Node {only} is not a supported LTS line, so the image "
                                    f"uses Node {DEFAULT}")


@dataclass(frozen=True)
class Choice:
    """The project's Node declarations and the major its image builds on."""

    declared: tuple[Declaration, ...]
    resolved: Resolved

    @property
    def source(self) -> Declaration | None:
        return self.declared[0] if self.declared else None

    def to_dict(self) -> dict[str, object]:
        return {
            "declared": [d.to_dict() for d in self.declared],
            "source": self.source.source if self.source else None,
            "spec": self.source.spec if self.source else None,
            "image_major": self.resolved.major,
            "candidates": list(self.resolved.candidates),
            "detail": self.resolved.reason,
        }


def choose(root: str | Path) -> Choice:
    """The major a generated image or workflow should use for this project."""
    found = declarations(root)
    if not found:
        return Choice(found, Resolved(DEFAULT, reason=f"no Node version is declared, so "
                                                      f"the image uses Node {DEFAULT}"))
    return Choice(found, resolve(found[0].spec))


def single(spec: str) -> int | None:
    """The one major a spec names, or None when it names more than one."""
    read = majors(spec)
    if read is None:
        return None
    allowed, bounded = read
    return next(iter(allowed)) if bounded and len(allowed) == 1 else None


def notices(root: str | Path, today: date | None = None) -> tuple[str, ...]:
    """Informational notices about the declared Node version.

    Never a rule and never part of the score or the gate.
    """
    day = today or date.today()
    found = declarations(root)
    out: list[str] = []
    if not found:
        return ()

    first = found[0]
    major = single(first.spec)
    if major is not None and major in END_OF_LIFE and END_OF_LIFE[major] <= day:
        line = (f"{first.source} declares Node {major} ({first.spec}), which reached end of "
                f"life on {END_OF_LIFE[major].isoformat()}. Move to Node {DEFAULT}, the "
                f"current LTS line.")
        if major == 20 and first.source == "package.json engines.node":
            line += (" Rule BLD-005 of the 1.x ruleset still asks for a Node 20 pin; that "
                     "rule changes in 2.0.")
        out.append(line + " This notice does not affect the score or the gate.")

    elif major is not None and major not in SUPPORTED:
        out.append(f"{first.source} declares Node {major} ({first.spec}), which is not an LTS "
                   f"line ProdPilot builds on, so generated files use Node {DEFAULT} while "
                   f"Render would build with Node {major}. This notice does not affect the "
                   f"score or the gate.")

    differ = {m for m in (single(d.spec) for d in found) if m is not None}
    if len(differ) > 1:
        listed = ", ".join(f"{d.source} ({d.spec})" for d in found)
        out.append(f"The project declares different Node versions: {listed}. Render uses "
                   f"{first.source}, and so does ProdPilot. This notice does not affect the "
                   f"score or the gate.")
    return tuple(out)

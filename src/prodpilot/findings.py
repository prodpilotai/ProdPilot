"""The shape every check in the audit engine reports.

Scope is Phase 2 module 2.4. Finding and Status began in astchecks because the
AST checks were built first, and filechecks then imported them from there. That
left the dependency running from the file checks to the AST checks, which is
backwards: neither family depends on the other, and both depend on this.

Moving them here is deliberately non-breaking. astchecks re-exports both names,
so existing imports keep working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Status(str, Enum):
    """Outcome of one check against one file or project."""

    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"
    UNPARSED = "unparsed"


@dataclass(frozen=True)
class Finding:
    """One check result.

    A finding is always tied to a rule_id and a file. line is the position of
    the violation when there is one to point at. SKIPPED means the rule does
    not apply here, so scoring should not count it either way. UNPARSED means
    the source could not be read, which is never treated as a pass.
    """

    rule_id: str
    status: Status
    file: str
    line: int | None = None
    detail: str = ""

    @property
    def counts(self) -> bool:
        """Whether this finding contributes to a score at all."""
        return self.status in (Status.PASS, Status.FAIL)

    @property
    def passed(self) -> bool:
        return self.status is Status.PASS

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "status": self.status.value,
            "file": self.file,
            "line": self.line,
            "detail": self.detail,
        }

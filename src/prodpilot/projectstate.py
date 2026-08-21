"""Per project state stored in .env.prodpilot.

Scope is Phase 1 module 1.3. This establishes the convention and the read and
write helpers. The values it will eventually carry, service_id, deploy_id and
service_url, are produced by ProdPush in Phase 6. Nothing here deploys
anything.

The split matters. Credentials belong to the developer and live in
~/.prodpilot/config.toml, outside every repository. Deployment state belongs to
one project and lives beside it, in a file that must be gitignored.

Design principle 5 in Section 2.1 of the Complete Solution Document states that
secrets are never written to any committed file. This module enforces that
directly: a write that looks like a credential is rejected rather than stored.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_STATE_FILENAME = ".env.prodpilot"
GITIGNORE_FILENAME = ".gitignore"

# Keys ProdPush will populate in Phase 6. Declared here so the convention is
# fixed before anything writes to it, not invented later at the call site.
SERVICE_ID_KEY = "PRODPILOT_SERVICE_ID"
DEPLOY_ID_KEY = "PRODPILOT_DEPLOY_ID"
SERVICE_URL_KEY = "PRODPILOT_SERVICE_URL"

KNOWN_STATE_KEYS = (SERVICE_ID_KEY, DEPLOY_ID_KEY, SERVICE_URL_KEY)

# A key matching any of these is a credential, and a credential must never be
# written into a project directory. Matched on the key name, since that is what
# the caller controls.
CREDENTIAL_KEY_PATTERN = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|CREDENTIAL)",
    re.IGNORECASE,
)

_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

STATE_FILE_HEADER = (
    "# ProdPilot project state. Generated file, safe to delete.\n"
    "# Holds deployment identifiers for this project only.\n"
    "# Never put credentials here. Those live in ~/.prodpilot/config.toml.\n"
)


class ProjectStateError(Exception):
    """Raised when project state cannot be read or written."""


class CredentialInProjectError(ProjectStateError):
    """Raised when a caller tries to write a credential into a project tree.

    Fails closed on purpose. Section 2.1 forbids writing a secret to any file
    inside a repository, so this is refused rather than warned about.
    """


@dataclass(frozen=True)
class GitignoreStatus:
    """Whether the state file is excluded from version control."""

    gitignore_exists: bool
    is_ignored: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "gitignore_exists": self.gitignore_exists,
            "is_ignored": self.is_ignored,
            "detail": self.detail,
        }


def state_path(project_path: str | Path) -> Path:
    """Return the path to the state file for a project."""
    return Path(project_path).expanduser().resolve() / PROJECT_STATE_FILENAME


def _reject_credential_keys(values: dict[str, str]) -> None:
    offenders = sorted(k for k in values if CREDENTIAL_KEY_PATTERN.search(k))
    if offenders:
        raise CredentialInProjectError(
            "refusing to write credential-like keys into a project directory: "
            + ", ".join(offenders)
            + ". Store credentials with `prodpilot setup` instead"
        )


def _validate_keys(values: dict[str, str]) -> None:
    malformed = sorted(k for k in values if not _KEY_PATTERN.match(k))
    if malformed:
        raise ProjectStateError(
            "invalid environment key names: " + ", ".join(malformed)
        )


def read_project_state(project_path: str | Path) -> dict[str, str]:
    """Read the state file, returning an empty mapping when it does not exist.

    Unparseable lines are skipped with a warning rather than raising, since the
    file is developer visible and may be hand edited.
    """
    path = state_path(project_path)
    if not path.is_file():
        return {}

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProjectStateError(f"cannot read {path}: {exc}") from exc

    values: dict[str, str] = {}
    for number, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            logger.warning("skipping line %s of %s, no assignment", number, path)
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not _KEY_PATTERN.match(key):
            logger.warning("skipping line %s of %s, invalid key", number, path)
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def write_project_state(
    project_path: str | Path, values: dict[str, str], merge: bool = True
) -> Path:
    """Write project state, refusing anything that looks like a credential.

    Existing values are merged by default so one caller does not erase another
    caller's keys.
    """
    _validate_keys(values)
    _reject_credential_keys(values)

    project = Path(project_path).expanduser().resolve()
    if not project.is_dir():
        raise ProjectStateError(f"project path is not a directory: {project}")

    merged = dict(read_project_state(project)) if merge else {}
    merged.update({k: str(v) for k, v in values.items()})

    path = state_path(project)
    body = "".join(f"{key}={merged[key]}\n" for key in sorted(merged))
    try:
        path.write_text(STATE_FILE_HEADER + body, encoding="utf-8")
    except OSError as exc:
        raise ProjectStateError(f"cannot write {path}: {exc}") from exc

    logger.info("wrote %s keys to %s", len(merged), path)
    return path


def check_gitignored(project_path: str | Path) -> GitignoreStatus:
    """Report whether .env.prodpilot is excluded from version control."""
    project = Path(project_path).expanduser().resolve()
    gitignore = project / GITIGNORE_FILENAME

    if not gitignore.is_file():
        return GitignoreStatus(
            gitignore_exists=False,
            is_ignored=False,
            detail=f"no {GITIGNORE_FILENAME} at the project root",
        )

    try:
        lines = gitignore.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ProjectStateError(f"cannot read {gitignore}: {exc}") from exc

    for line in lines:
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.rstrip("/") in (PROJECT_STATE_FILENAME, PROJECT_STATE_FILENAME.lstrip(".")):
            return GitignoreStatus(
                gitignore_exists=True,
                is_ignored=True,
                detail=f"{PROJECT_STATE_FILENAME} is listed in {GITIGNORE_FILENAME}",
            )
        if entry in (".env*", "*.prodpilot", ".env.*"):
            return GitignoreStatus(
                gitignore_exists=True,
                is_ignored=True,
                detail=f"covered by the pattern {entry}",
            )

    return GitignoreStatus(
        gitignore_exists=True,
        is_ignored=False,
        detail=f"{PROJECT_STATE_FILENAME} is not excluded by {GITIGNORE_FILENAME}",
    )


def ensure_gitignored(project_path: str | Path) -> GitignoreStatus:
    """Add .env.prodpilot to .gitignore when it is not already excluded."""
    status = check_gitignored(project_path)
    if status.is_ignored:
        return status

    project = Path(project_path).expanduser().resolve()
    gitignore = project / GITIGNORE_FILENAME
    block = f"\n# ProdPilot project state\n{PROJECT_STATE_FILENAME}\n"

    try:
        if gitignore.is_file():
            existing = gitignore.read_text(encoding="utf-8")
            separator = "" if existing.endswith("\n") else "\n"
            gitignore.write_text(existing + separator + block, encoding="utf-8")
        else:
            gitignore.write_text(block.lstrip("\n"), encoding="utf-8")
    except OSError as exc:
        raise ProjectStateError(f"cannot update {gitignore}: {exc}") from exc

    logger.info("added %s to %s", PROJECT_STATE_FILENAME, gitignore)
    return check_gitignored(project)

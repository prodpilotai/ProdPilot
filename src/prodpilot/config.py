"""User level configuration and credential storage for ProdPilot.

Scope is Phase 1 module 1.3. Credentials live in ~/.prodpilot/config.toml,
outside every project directory, because a GitHub token and a Render API key
belong to the developer rather than to any one repository.

Design principle 5 in Section 2.1 of the Complete Solution Document states that
secrets are never written to any committed file. This module is the mechanism
that upholds it: nothing here ever writes a credential inside a project tree,
and the store it does write is restricted to the current user.

Reading uses tomllib from the standard library. Writing uses tomli_w, since
tomllib is read only and hand rolled TOML escaping is a correctness risk.
"""

from __future__ import annotations

import logging
import os
import platform
import stat
import subprocess
import tomllib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import tomli_w

logger = logging.getLogger(__name__)

CONFIG_DIR_NAME = ".prodpilot"
CONFIG_FILE_NAME = "config.toml"

# Lets the test suite point at a scratch directory instead of the real home.
# Without it every test run would read and overwrite the developer's own
# credentials.
CONFIG_HOME_ENV_VAR = "PRODPILOT_CONFIG_HOME"

CREDENTIALS_TABLE = "credentials"
GITHUB_TOKEN_KEY = "github_token"
RENDER_API_KEY_KEY = "render_api_key"

REQUIRED_CREDENTIALS = (GITHUB_TOKEN_KEY, RENDER_API_KEY_KEY)

OWNER_ONLY_MODE = 0o600
OWNER_ONLY_DIR_MODE = 0o700


class ConfigError(Exception):
    """Raised when the configuration store cannot be read or written."""


class PermissionState(str, Enum):
    """Whether the credential file is readable only by the current user."""

    RESTRICTED = "restricted"
    UNRESTRICTED = "unrestricted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PermissionReport:
    """The verified access state of the credential file.

    Verified rather than assumed. On Windows os.chmod does not restrict access,
    so applying a mode and trusting it would report protection that is not
    there.
    """

    state: PermissionState
    detail: str
    principals: tuple[str, ...] = ()

    @property
    def is_restricted(self) -> bool:
        return self.state is PermissionState.RESTRICTED

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "detail": self.detail,
            "principals": list(self.principals),
        }


@dataclass(frozen=True)
class Credentials:
    """The credentials ProdPilot needs, as currently stored."""

    github_token: str | None = None
    render_api_key: str | None = None

    @property
    def missing(self) -> tuple[str, ...]:
        absent = []
        if not self.github_token:
            absent.append(GITHUB_TOKEN_KEY)
        if not self.render_api_key:
            absent.append(RENDER_API_KEY_KEY)
        return tuple(absent)

    @property
    def is_complete(self) -> bool:
        return not self.missing


def config_home() -> Path:
    """Return the directory that holds the ProdPilot config directory."""
    override = os.environ.get(CONFIG_HOME_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path.home()


def config_dir() -> Path:
    """Return ~/.prodpilot, or its test override."""
    return config_home() / CONFIG_DIR_NAME


def config_path() -> Path:
    """Return the full path to config.toml."""
    return config_dir() / CONFIG_FILE_NAME


def _current_windows_principal() -> str:
    """Return the DOMAIN\\user string icacls expects."""
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain else user


def _run_icacls(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["icacls", *args],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )


def _restrict_windows(path: Path) -> PermissionReport:
    """Break ACL inheritance and grant the current user sole access.

    os.chmod cannot express this on Windows. It sets the read only flag and
    nothing more, which leaves inherited entries for other principals intact.
    """
    principal = _current_windows_principal()
    if not principal:
        return PermissionReport(
            PermissionState.UNKNOWN,
            "cannot determine the current Windows user, permissions unchanged",
        )

    result = _run_icacls([str(path), "/inheritance:r", "/grant:r", f"{principal}:F"])
    if result.returncode != 0:
        logger.warning("icacls failed on %s: %s", path, result.stderr.strip())
        return PermissionReport(
            PermissionState.UNKNOWN,
            f"could not apply access control entries: {result.stderr.strip()}",
        )
    return verify_permissions(path)


def _read_windows_principals(path: Path) -> tuple[str, ...] | None:
    """Return the principals holding an access control entry on the file."""
    result = _run_icacls([str(path)])
    if result.returncode != 0:
        return None

    principals: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("successfully processed"):
            continue
        if line.lower().startswith("failed processing"):
            continue
        # Lines look like "<path> DOMAIN\user:(F)" or a continuation
        # "DOMAIN\user:(F)". Strip any leading path, then take the part before
        # the trailing permission mask.
        candidate = line
        if str(path) in candidate:
            candidate = candidate.replace(str(path), "", 1).strip()
        if ":" not in candidate:
            continue
        principal = candidate.rsplit(":", 1)[0].strip()
        if principal:
            principals.append(principal)
    return tuple(principals)


def verify_permissions(path: Path) -> PermissionReport:
    """Report who can actually read the credential file.

    Never assumes the applied mode took effect. Reads the real state back.
    """
    if not path.exists():
        return PermissionReport(PermissionState.UNKNOWN, "file does not exist")

    if platform.system() == "Windows":
        principals = _read_windows_principals(path)
        if principals is None:
            return PermissionReport(
                PermissionState.UNKNOWN, "could not read access control entries"
            )
        expected = _current_windows_principal().lower()
        others = tuple(p for p in principals if p.lower() != expected)
        if others:
            return PermissionReport(
                PermissionState.UNRESTRICTED,
                "other accounts can read this file: " + ", ".join(others),
                principals,
            )
        return PermissionReport(
            PermissionState.RESTRICTED,
            "access is limited to the current user",
            principals,
        )

    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        return PermissionReport(
            PermissionState.UNRESTRICTED,
            f"group or other permission bits are set: {oct(mode)}",
        )
    return PermissionReport(
        PermissionState.RESTRICTED, f"file mode is {oct(mode)}"
    )


def restrict_permissions(path: Path) -> PermissionReport:
    """Limit the file to the current user, then verify the result."""
    if platform.system() == "Windows":
        return _restrict_windows(path)

    try:
        os.chmod(path, OWNER_ONLY_MODE)
    except OSError as exc:
        logger.warning("cannot chmod %s: %s", path, exc)
        return PermissionReport(
            PermissionState.UNKNOWN, f"could not change file mode: {exc}"
        )
    return verify_permissions(path)


def load_credentials() -> Credentials:
    """Read stored credentials, returning empty values when none exist yet."""
    path = config_path()
    if not path.is_file():
        return Credentials()

    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    table = document.get(CREDENTIALS_TABLE)
    if not isinstance(table, dict):
        return Credentials()

    def value_of(key: str) -> str | None:
        raw = table.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return None

    return Credentials(
        github_token=value_of(GITHUB_TOKEN_KEY),
        render_api_key=value_of(RENDER_API_KEY_KEY),
    )


def save_credentials(credentials: Credentials) -> PermissionReport:
    """Write credentials to config.toml and restrict access to the current user.

    Existing keys outside the credentials table are preserved, so unrelated
    settings added later are not destroyed by a rerun of setup.
    """
    directory = config_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"cannot create {directory}: {exc}") from exc

    if platform.system() != "Windows":
        try:
            os.chmod(directory, OWNER_ONLY_DIR_MODE)
        except OSError as exc:
            logger.warning("cannot chmod %s: %s", directory, exc)

    path = config_path()
    document: dict[str, object] = {}
    if path.is_file():
        try:
            with path.open("rb") as handle:
                document = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"cannot update {path}: {exc}") from exc

    table = document.get(CREDENTIALS_TABLE)
    stored = dict(table) if isinstance(table, dict) else {}
    if credentials.github_token:
        stored[GITHUB_TOKEN_KEY] = credentials.github_token
    if credentials.render_api_key:
        stored[RENDER_API_KEY_KEY] = credentials.render_api_key
    document[CREDENTIALS_TABLE] = stored

    try:
        with path.open("wb") as handle:
            tomli_w.dump(document, handle)
    except OSError as exc:
        raise ConfigError(f"cannot write {path}: {exc}") from exc

    logger.info("wrote credentials to %s", path)
    return restrict_permissions(path)

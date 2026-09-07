"""Pre-flight checks, the first stage of ProdPush.

Scope is Phase 6 module 6.1. Section 7 stage 1 states it in one line: verify a
Git repository exists, that the remote origin is a GitHub URL, and that the
Render API key and GitHub token are present, with missing items collected from
the developer and stored in local, ignored config.

This module verifies. It seals no environment, builds no image, and calls no
deployment API. Those are 6.2, 6.3 and 6.5.

Where the credentials come from
-------------------------------
Module 1.3's store, read through config.load_credentials, which is the only way
credentials are loaded anywhere in this package.

Section 7 says missing items are collected from the developer, and 1.3 already
built that: prodpilot setup prompts for both values, writes them to
~/.prodpilot/config.toml, restricts the file to the current user, and never
echoes a secret back. So a failing credential check here names that command
rather than prompting on its own. A second collection path would be a second
place a secret could be mishandled, and there is no reason to have one.

Nothing here reads a secret's value. It asks whether one is present.

What a result has to carry
--------------------------
Stage 2 acts on a failure without working out again what went wrong, so every
check reports three things: whether it passed, what was actually found, and what
the developer should do about it. A caller that wants to print something useful
should never have to re-run a check to find out why it failed.

The four checks are independent. A project path that does not exist fails the
two repository checks and leaves the credential checks alone, because whether a
token is stored has nothing to do with the project being deployed. Reporting all
four every time is what lets a developer fix everything in one pass rather than
one round trip per problem.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from prodpilot import entropy
from prodpilot.config import ConfigError, load_credentials

logger = logging.getLogger(__name__)

# The command module 1.3 already provides for collecting what is missing.
SETUP = "prodpilot setup"

# Hosts that count as GitHub. Section 7 stage 4 pushes over token based HTTPS to
# github.com, so an origin anywhere else is not something ProdPush can deploy.
HOSTS = ("github.com", "www.github.com")

REPO = "repo"
ORIGIN = "origin"
RENDER_KEY = "render_key"
GITHUB_TOKEN = "github_token"


@dataclass(frozen=True)
class Check:
    """One pre-flight condition and what was found.

    detail says what is actually the case. fix says what to do about it, and is
    empty when the check passed.
    """

    name: str
    ok: bool
    detail: str
    fix: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail, "fix": self.fix}


@dataclass(frozen=True)
class Preflight:
    """The result of all four checks against one project."""

    project: str
    checks: tuple[Check, ...]

    @property
    def ready(self) -> bool:
        """Whether ProdPush may proceed to stage 2."""
        return all(check.ok for check in self.checks)

    @property
    def failed(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.ok)

    def get(self, name: str) -> Check:
        """One check by name, so a caller can act on a specific condition."""
        for check in self.checks:
            if check.name == name:
                return check
        raise KeyError(f"no pre-flight check named {name}")

    def to_dict(self) -> dict[str, object]:
        return {
            "project": self.project,
            "ready": self.ready,
            "checks": [check.to_dict() for check in self.checks],
            "failed": [check.name for check in self.failed],
        }

    def summary(self) -> str:
        if self.ready:
            return f"{self.project}: ready for deployment, all 4 pre-flight checks passed"
        names = ", ".join(check.name for check in self.failed)
        return (
            f"{self.project}: not ready, {len(self.failed)} of {len(self.checks)} "
            f"pre-flight checks failed: {names}"
        )


def git(root: Path, *args: str) -> tuple[int, str]:
    """Run one git command in a project, returning its code and output.

    Git may be absent or may hang on a misconfigured repository, so both are
    handled rather than allowed to reach the caller as an unexpected error.
    """
    try:
        done = subprocess.run(
            ("git",) + args,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        return 1, "git is not installed or is not on PATH"
    except subprocess.TimeoutExpired:
        return 1, "git did not respond within 15 seconds"
    except OSError as exc:
        return 1, str(exc)
    return done.returncode, (done.stdout or done.stderr).strip()


def check_repo(root: Path) -> Check:
    """A Git repository has to exist before anything can be pushed."""
    if not root.exists():
        return Check(REPO, False, f"{root} does not exist",
                     "point ProdPush at an existing project directory")
    if not root.is_dir():
        return Check(REPO, False, f"{root} is not a directory",
                     "point ProdPush at a project directory")
    if not entropy.is_repo(root):
        return Check(REPO, False, "the project is not a Git repository",
                     "run git init and commit the project before deploying")
    return Check(REPO, True, "the project is a Git repository")


def check_origin(root: Path) -> Check:
    """The remote has to be GitHub, since that is what stage 4 pushes to."""
    if not root.is_dir() or not entropy.is_repo(root):
        return Check(ORIGIN, False, "there is no repository to read a remote from",
                     "run git init and add a GitHub remote named origin")

    code, output = git(root, "remote", "get-url", "origin")
    if code != 0:
        return Check(ORIGIN, False, "no remote named origin is configured",
                     "run git remote add origin with your GitHub repository URL")

    host = host_of(output)
    if host is None:
        return Check(ORIGIN, False, f"the origin remote is not a URL: {output}",
                     "set origin to a GitHub HTTPS or SSH URL")
    if host not in HOSTS:
        return Check(ORIGIN, False, f"the origin remote points at {host}, not GitHub",
                     "set origin to a GitHub repository URL")
    return Check(ORIGIN, True, f"the origin remote is on {host}")


def host_of(remote: str) -> str | None:
    """The host of a Git remote, in either the HTTPS or the SSH form.

    Git accepts git@github.com:owner/repo.git, which urlparse reads as a path
    rather than a host, so that form is handled before parsing.
    """
    remote = remote.strip()
    if not remote:
        return None
    if "://" not in remote and "@" in remote and ":" in remote:
        return remote.split("@", 1)[1].split(":", 1)[0].lower() or None
    parsed = urlparse(remote)
    if not parsed.scheme or not parsed.hostname:
        return None
    return parsed.hostname.lower()


def check_keys() -> tuple[Check, Check]:
    """Both credentials, read through module 1.3 and never printed.

    An unreadable store is reported as both being absent, because a store that
    cannot be parsed is not a store that holds a usable credential.
    """
    try:
        stored = load_credentials()
    except ConfigError as exc:
        logger.warning("cannot read the credential store: %s", exc)
        detail = f"the credential store could not be read: {exc}"
        return (
            Check(RENDER_KEY, False, detail, f"fix the file, then run {SETUP}"),
            Check(GITHUB_TOKEN, False, detail, f"fix the file, then run {SETUP}"),
        )

    render = Check(
        RENDER_KEY, True, "a Render API key is stored"
    ) if stored.render_api_key else Check(
        RENDER_KEY, False, "no Render API key is stored", f"run {SETUP}"
    )
    token = Check(
        GITHUB_TOKEN, True, "a GitHub token is stored"
    ) if stored.github_token else Check(
        GITHUB_TOKEN, False, "no GitHub token is stored", f"run {SETUP}"
    )
    return render, token


def run(root: str | Path) -> Preflight:
    """Run all four pre-flight checks against one project.

    Never raises. Stage 2 has to be able to act on a result, and a project that
    cannot be inspected is a failed check rather than an exception.
    """
    base = Path(root)
    render, token = check_keys()
    result = Preflight(
        project=base.name or str(base),
        checks=(check_repo(base), check_origin(base), render, token),
    )
    logger.info("%s", result.summary())
    return result

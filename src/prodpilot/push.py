"""Git push, the fourth stage of ProdPush.

Scope is Phase 6 module 6.4. Section 7 stage 4 states it: stage the
ProdPilot-generated files, commit as 'chore: prodpilot production setup', push
to origin main over token-based HTTPS, and handle auth failures explicitly.

This module stages, commits and pushes. It creates no service and calls no
deployment API. Those are 6.5 and 6.6.

What actually gets staged
-------------------------
Only the files ProdPilot itself generates, and the list is derived from the
modules that generate them rather than written out here. Every STATIC template
that creates a file carries its path, and every DYNAMIC-PARAMETRIC shape that
creates a file carries its path, so GENERATED is the union of those two sets. If
Phase 3 ever learns to generate another file, this list grows with it and
nothing here needs editing.

Staging the whole working tree would sweep up whatever else the developer had
in progress and put it in a commit they did not write, which is not something a
deployment tool should do to someone's repository.

Why the ignore state is checked rather than trusted
----------------------------------------------------
git add refuses an ignored path on its own, but relying on that would mean this
module has no opinion about the one file it most needs an opinion about. So
every candidate is asked about explicitly through the same check module 6.2
uses, and an ignored path is skipped before git is asked to add it.

.env.production is doubly protected. It is not in GENERATED, because no
template or shape produces it, and module 6.2 refuses to write it at all unless
the project already ignores it. A test asserts it is never staged.

Why the token never reaches the repository
-------------------------------------------
The push URL carries the token, and that URL is passed to git as an argument
for one command rather than written into the remote configuration, so nothing
persists it. Git can still echo a URL back inside an error message, so every
string this module returns goes through scrub first. The token itself is read
through module 1.3, the same way module 6.1 reads it, and is never logged.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from prodpilot import sealing
from prodpilot.config import ConfigError, load_credentials
from prodpilot.extraction import SHAPES
from prodpilot.templates import TEMPLATES, Action

logger = logging.getLogger(__name__)

MESSAGE = "chore: prodpilot production setup"
BRANCH = "main"
REMOTE = "origin"

# Everything ProdPilot generates, taken from the modules that generate it.
GENERATED: tuple[str, ...] = tuple(sorted(
    {t.path for t in TEMPLATES.values() if t.action is Action.CREATE_FILE and t.path}
    | {s.path for s in SHAPES.values() if getattr(s, "path", None)}
))

# The user git uses with a token over HTTPS. GitHub ignores the name and reads
# the token as the password, and this is the name it documents for the purpose.
USER = "x-access-token"


class Fail(str, Enum):
    """Why a push did not happen, kept apart so a caller can act on each."""

    NO_REMOTE = "no usable remote"
    NO_TOKEN = "no GitHub token"
    AUTH = "authentication failed"
    PERMISSION = "insufficient permissions"
    REJECTED = "push rejected"
    OTHER = "unclassified git failure"


# Matched in order against git's own output. The wording is git's and GitHub's,
# and each entry is a different thing for a developer to do about it.
REASONS: tuple[tuple[Fail, tuple[str, ...]], ...] = (
    (Fail.PERMISSION, (
        "permission to", "denied to", "write access to repository not granted",
        "403 forbidden",
    )),
    (Fail.AUTH, (
        "authentication failed", "invalid username or password",
        "could not read username", "terminal prompts disabled",
        "bad credentials", "401 unauthorized", "support for password authentication",
    )),
    (Fail.REJECTED, (
        "non-fast-forward", "! [rejected]", "fetch first",
        "updates were rejected", "tip of your current branch is behind",
    )),
)


@dataclass(frozen=True)
class Push:
    """The result module 6.5 consumes before creating a service."""

    project: str
    ok: bool
    staged: tuple[str, ...] = field(default_factory=tuple)
    commit: str = ""
    fail: Fail | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "project": self.project,
            "ok": self.ok,
            "staged": list(self.staged),
            "commit": self.commit,
            "fail": self.fail.value if self.fail else None,
            "detail": self.detail,
        }

    def summary(self) -> str:
        if self.ok:
            return (f"{self.project}: pushed {len(self.staged)} file(s) to "
                    f"{REMOTE}/{BRANCH}")
        return f"{self.project}: not pushed, {self.fail.value if self.fail else self.detail}"


def scrub(text: str, token: str | None) -> str:
    """Remove a token from anything about to be returned or logged."""
    if not text:
        return ""
    cleaned = text
    if token:
        cleaned = cleaned.replace(token, "***")
    return cleaned.strip()


def git(root: Path, *args: str, token: str | None = None) -> tuple[int, str]:
    """Run one git command, returning its code and its scrubbed output.

    Prompts are disabled so a missing credential fails immediately with a
    readable message instead of blocking on a terminal that is not there.
    """
    env = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    try:
        done = subprocess.run(
            ("git",) + args,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, **env},
        )
    except FileNotFoundError:
        return 1, "git is not installed or is not on PATH"
    except subprocess.TimeoutExpired:
        return 1, "git did not respond within 120 seconds"
    except OSError as exc:
        return 1, scrub(str(exc), token)
    return done.returncode, scrub((done.stdout or "") + (done.stderr or ""), token)


def classify(output: str) -> tuple[Fail, str]:
    """Which failure a git message describes, and the line that said so."""
    lowered = output.lower()
    for fail, needles in REASONS:
        for needle in needles:
            if needle in lowered:
                for line in output.splitlines():
                    if needle in line.lower():
                        return fail, line.strip()[:200]
                return fail, output.strip()[:200]
    return Fail.OTHER, output.strip()[:200]


def origin_url(root: Path) -> str | None:
    """The configured origin remote, or None when there is not one."""
    code, output = git(root, "remote", "get-url", REMOTE)
    return output.strip() if code == 0 and output.strip() else None


def https_url(remote: str) -> str | None:
    """The HTTPS form of a GitHub remote, whichever form it is written in."""
    remote = remote.strip()
    if remote.startswith("git@") and ":" in remote:
        host, _, path = remote.partition(":")
        return f"https://{host.split('@', 1)[1]}/{path.lstrip('/')}"
    if remote.startswith(("https://", "http://")):
        return remote
    if remote.startswith("ssh://git@"):
        rest = remote[len("ssh://git@"):]
        return f"https://{rest}"
    return None


def authed(url: str, token: str) -> str:
    """The push URL with the token in it, built for one command only."""
    scheme, _, rest = url.partition("://")
    rest = rest.split("@", 1)[-1]
    return f"{scheme}://{USER}:{token}@{rest}"


def local(remote: str) -> bool:
    """Whether a remote is a path on this machine rather than a server.

    Section 7 requires token HTTPS for a real deployment, and module 6.1 has
    already confirmed the origin is GitHub before this stage runs. A local
    remote is what a test pushes to, and it needs no token because there is no
    server to authenticate against.
    """
    remote = remote.strip()
    if remote.startswith("file://"):
        return True
    if remote.startswith(("http://", "https://", "ssh://", "git@")):
        return False
    return Path(remote).exists()


def target(remote: str, token: str) -> tuple[str | None, bool]:
    """Where to push, and whether the token is carried in the URL."""
    url = https_url(remote)
    if url:
        return authed(url, token), True
    if local(remote):
        return remote, False
    return None, False


def pending(root: Path) -> tuple[str, ...]:
    """Which generated files exist and are not ignored, so may be staged."""
    out = []
    for name in GENERATED:
        if not (root / name).exists():
            continue
        if sealing.ignored(root, name):
            logger.info("not staging %s, the project ignores it", name)
            continue
        out.append(name)
    return tuple(out)


def run(root: str | Path, token: str | None = None,
        message: str = MESSAGE, branch: str = BRANCH) -> Push:
    """Stage the generated files, commit them, and push over token HTTPS.

    token defaults to the one module 1.3 stores, read the same way module 6.1
    reads it. Never raises: module 6.5 reads the result to decide whether to
    create a service.
    """
    base = Path(root)
    name = base.name or str(base)

    if token is None:
        try:
            token = load_credentials().github_token
        except ConfigError as exc:
            return Push(name, False, fail=Fail.NO_TOKEN,
                        detail=f"the credential store could not be read: {exc}")
    if not token:
        return Push(name, False, fail=Fail.NO_TOKEN,
                    detail="no GitHub token is stored, run prodpilot setup")

    remote = origin_url(base)
    if not remote:
        return Push(name, False, fail=Fail.NO_REMOTE,
                    detail=f"no remote named {REMOTE} is configured")
    url, carries = target(remote, token)
    if not url:
        return Push(name, False, fail=Fail.NO_REMOTE,
                    detail=f"the {REMOTE} remote is not an HTTPS or SSH URL")

    staged = pending(base)
    if staged:
        code, output = git(base, "add", "--", *staged, token=token)
        if code != 0:
            fail, line = classify(output)
            return Push(name, False, staged=staged, fail=fail, detail=line)

    code, output = git(base, "commit", "-m", message, token=token)
    if code != 0 and "nothing to commit" not in output.lower():
        fail, line = classify(output)
        return Push(name, False, staged=staged, fail=fail, detail=line)

    code, head = git(base, "rev-parse", "HEAD", token=token)
    commit = head.strip() if code == 0 else ""

    code, output = git(base, "push", url, f"HEAD:{branch}", token=token)
    if code != 0:
        fail, line = classify(output)
        result = Push(name, False, staged=staged, commit=commit, fail=fail, detail=line)
        logger.warning("%s", result.summary())
        return result

    result = Push(name, True, staged=staged, commit=commit, detail=output[:200])
    logger.info("%s", result.summary())
    return result

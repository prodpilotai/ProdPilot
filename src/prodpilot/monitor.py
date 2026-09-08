"""Deploy monitoring, the sixth stage of ProdPush.

Scope is Phase 6 module 6.6. Section 7 stage 6 states it: poll the Render API
every 15 seconds, keep polling while the build is in progress, proceed when it
is live, pull the build logs and classify when it has failed, and time out after
10 minutes.

This works against the provider interface rather than against Render. It takes
anything with poll_status and get_logs, so the second provider module 6.9 asks
about would need no change here.

Why a timeout is not a failure
-------------------------------
Render reporting failed means the build finished and did not work, and the logs
say why. A timeout means nothing is known: the deploy may still be running and
may yet succeed. Those call for different things from a developer, so they are
different outcomes rather than one unhappy path, and a caller can tell them
apart without reading a string.

Why the classification is module 6.3's
---------------------------------------
A build that fails on Render fails for the same reasons it fails on a laptop, so
the taxonomy is the same taxonomy. classify is imported from buildtest rather
than restated, which means the five categories, the wording both Docker builders
produce, and the rule separating a missing dependency from a failing build
script are all defined once. An unmatched log goes to manual review here for the
same reason it does there.

Why the clock is injected
--------------------------
Fifteen seconds and ten minutes are what Section 7 specifies and what runs in
production. A test that waited them out would take ten minutes to prove one
branch, so the sleep and the clock are arguments, exactly as module 5.1 does for
rate limit waiting.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from prodpilot.buildtest import Fault, classify
from prodpilot.provider import Status

logger = logging.getLogger(__name__)

# Section 7 stage 6, verbatim: every 15 seconds, give up after 10 minutes.
EVERY = 15.0
LIMIT = 600.0


class Outcome(str, Enum):
    """How watching a deploy ended."""

    LIVE = "live"
    FAILED = "failed"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True)
class Watch:
    """What the monitor saw, for stage 7 to act on."""

    deploy_id: str
    outcome: Outcome
    polls: int = 0
    waited: float = 0.0
    fault: Fault | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.LIVE

    @property
    def review(self) -> bool:
        """Whether a person has to look at this rather than the loop.

        A failure the taxonomy did not match, an error reaching the provider,
        and a timeout all need a person. Only a classified failure may go to
        the loop, which is module 6.3's rule and Section 12's mitigation.
        """
        if self.outcome is Outcome.FAILED:
            return self.fault is None
        return self.outcome in (Outcome.TIMEOUT, Outcome.ERROR)

    @property
    def actionable(self) -> bool:
        return self.outcome is Outcome.FAILED and self.fault is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "deploy_id": self.deploy_id,
            "outcome": self.outcome.value,
            "polls": self.polls,
            "waited": round(self.waited, 1),
            "fault": self.fault.value if self.fault else None,
            "detail": self.detail,
            "review": self.review,
            "actionable": self.actionable,
        }

    def summary(self) -> str:
        if self.outcome is Outcome.LIVE:
            return f"{self.deploy_id}: live after {self.polls} poll(s)"
        if self.outcome is Outcome.FAILED:
            said = self.fault.value if self.fault else "unclassified, needs manual review"
            return f"{self.deploy_id}: failed, {said}"
        if self.outcome is Outcome.TIMEOUT:
            return f"{self.deploy_id}: still building after {self.waited:.0f}s, gave up"
        return f"{self.deploy_id}: could not be watched, {self.detail}"


def watch(provider, deploy_id: str, every: float = EVERY, limit: float = LIMIT,
          sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic) -> Watch:
    """Poll one deploy until it is live, has failed, or the limit runs out.

    provider is anything offering the interface's poll_status and get_logs.
    Never raises: stage 7 reads the result to decide whether to smoke test.
    """
    started = clock()
    polls = 0

    while True:
        try:
            status = provider.poll_status(deploy_id)
        except Exception as exc:
            waited = clock() - started
            logger.warning("could not read the deploy status: %s", exc)
            return Watch(deploy_id, Outcome.ERROR, polls, waited,
                         detail=f"{type(exc).__name__}: {exc}")
        polls += 1
        waited = clock() - started

        if status is Status.LIVE:
            result = Watch(deploy_id, Outcome.LIVE, polls, waited,
                           detail="the deploy is live")
            logger.info("%s", result.summary())
            return result

        if status is Status.FAILED:
            fault, line = failure(provider, deploy_id)
            result = Watch(deploy_id, Outcome.FAILED, polls, waited, fault, line)
            logger.warning("%s", result.summary())
            return result

        if waited + every > limit:
            result = Watch(deploy_id, Outcome.TIMEOUT, polls, waited,
                           detail=f"still building after {limit:.0f}s")
            logger.warning("%s", result.summary())
            return result

        sleep(every)


def failure(provider, deploy_id: str) -> tuple[Fault | None, str]:
    """Why a deploy failed, classified by module 6.3's taxonomy.

    Logs that cannot be fetched leave the failure unclassified, which sends it
    to a person rather than inventing a category for it.
    """
    try:
        logs = provider.get_logs(deploy_id)
    except Exception as exc:
        logger.warning("could not fetch the build logs: %s", exc)
        return None, f"the deploy failed and the logs could not be read: {exc}"

    if not logs:
        return None, "the deploy failed and Render returned no build logs"
    fault, line = classify(logs)
    if fault is None:
        return None, "the deploy failed for a reason the taxonomy does not cover"
    return fault, line

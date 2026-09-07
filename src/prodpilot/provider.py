"""The deployment provider interface ProdPush is built against.

Scope is the interface only. Section 7.2 of the Complete Solution Document says
Render is the only target in v1, but ProdPush is built against a small provider
interface: deploy, poll_status, get_logs, set_env. Those four methods are
defined here and nothing implements them yet.

The Render implementation is modules 6.5 and 6.6. Module 6.9 later verifies that
a second provider could be added as another implementation of these four
methods rather than a rewrite, and the Implementation Phases document is
explicit that this interface is designed alongside 6.1 rather than retrofitted
once the Render code exists. That is the whole reason it is here today.

Why a Protocol rather than an abstract base class
--------------------------------------------------
Every seam in this codebase so far is structural. Ten of them are plain Callable
aliases, and nothing anywhere inherits from a base class to be usable. A
provider is the first seam that is one object with four related methods rather
than a single function, so an alias will not carry it, but forcing
RenderProvider to inherit from something would be the only inheritance in the
package and would buy nothing.

A Protocol keeps the existing style: an implementation conforms by having the
right methods, not by declaring a relationship. It also means a test double
needs no import from here.

It is runtime_checkable so a test can assert conformance, with one caveat worth
knowing: isinstance against a runtime checkable Protocol only confirms the
methods exist, not that their signatures match. The tests check signatures
separately rather than trusting that.

Why the types are real rather than dictionaries
------------------------------------------------
The three small records below come straight from Section 7's own description of
stages 5 and 6: a service is created from a repository URL, a branch, a build
command and a start command with environment variables injected through the API,
and it returns a service id, a deploy id and a URL, after which the deploy is
polled until it is live or has failed.

Passing dictionaries would have been less work today and would have guaranteed
the rewrite this module exists to prevent, so the shape is named now while it is
cheap.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class Status(str, Enum):
    """Where a deployment has got to.

    The three states Section 7 stage 6 acts on: keep polling, proceed, or pull
    the logs and classify the failure.
    """

    BUILDING = "building"
    LIVE = "live"
    FAILED = "failed"


@dataclass(frozen=True)
class Service:
    """What a provider needs in order to create a service.

    env carries the values from the sealed environment, which module 6.2
    produces. They travel through the provider API and are never committed,
    which is the rule Section 7 stage 2 and stage 5 both state.
    """

    name: str
    repo: str
    branch: str
    build: str
    start: str
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Deployment:
    """What a provider returns once a service exists.

    The three identifiers Section 7 stage 5 says are stored locally, so a later
    stage can poll, fetch logs, and smoke test without asking again.
    """

    service_id: str
    deploy_id: str
    url: str


@runtime_checkable
class Provider(Protocol):
    """The four methods a deployment target has to offer.

    Nothing implements this yet. Module 6.5 supplies deploy and set_env against
    the Render API, and 6.6 supplies poll_status and get_logs.
    """

    def deploy(self, service: Service) -> Deployment:
        """Create the service and start its first deploy."""
        ...

    def poll_status(self, deploy_id: str) -> Status:
        """Ask where one deploy has got to."""
        ...

    def get_logs(self, deploy_id: str) -> str:
        """Fetch the build logs for one deploy, for classifying a failure."""
        ...

    def set_env(self, service_id: str, env: Mapping[str, str]) -> None:
        """Replace the environment variables on an existing service."""
        ...


# The four names, kept as data so module 6.9 can check the interface it is
# meant to verify rather than restating it from the document.
METHODS = ("deploy", "poll_status", "get_logs", "set_env")

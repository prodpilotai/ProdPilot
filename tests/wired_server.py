"""A ProdPilot MCP server with the module 3.6 seam filled by a test double.

Module 3.6 does not exist yet, so the server the CLI launches has no verifier
and cannot give an outcome. This entry point exists so the stdio tests can drive
the full path anyway, with a stand in supplying the verdict.

The stand in is not a fixed true. PRODPILOT_TEST_PASSING lists the rule ids it
reports as passing and every other rule fails, so a test controls the verdict
and both answers can be proven over the real transport. Anything that always
agreed would make the tests pass without proving the wiring carries a refusal.

Nothing here is imported by the package. It is a test harness and it lives in
tests for that reason.
"""

from __future__ import annotations

import os
import sys

from prodpilot.cli import configure_logging
from prodpilot.server import build_server

PASSING = "PRODPILOT_TEST_PASSING"


def verify(rule_id: str) -> bool:
    """Stand in for module 3.6, answering from the environment."""
    listed = os.environ.get(PASSING, "")
    return rule_id in {r.strip() for r in listed.split(",") if r.strip()}


def main() -> int:
    configure_logging()
    print(f"verification double active, passing={os.environ.get(PASSING, '')}",
          file=sys.stderr)
    build_server(verify=verify).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

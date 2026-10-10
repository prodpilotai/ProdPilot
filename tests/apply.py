"""The contract executor the loop tests use, now part of the package.

It moved to prodpilot.applying in 1.1 so the dry-run preview can share it. The
names the tests import are re-exported unchanged.
"""

from prodpilot.applying import (  # noqa: F401
    applier,
    declare,
    declare_keys,
    end_of,
    first,
    insertion,
    place,
    present,
    replace,
    write,
)

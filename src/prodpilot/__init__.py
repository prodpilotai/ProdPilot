"""ProdPilot: a layered MCP-based system for automated production readiness.

This package is built in the phase order defined by the Phased Implementation
Plan. The current build covers Phase 1 module 1.1, the MCP server skeleton.
"""

# The single source of the version. pyproject.toml reads it through hatchling,
# the release workflow checks the tag against it, and a test holds the GitHub
# Action's default version to it.
__version__ = "1.1.0"

__all__ = ["__version__"]

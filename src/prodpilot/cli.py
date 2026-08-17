"""Command line entry point for ProdPilot.

Exposes the MCP server as a launchable command so an MCP client can start it as
a subprocess. Section 9 of the Complete Solution Document specifies Typer for
the CLI.
"""

from __future__ import annotations

import logging
import sys

import typer

from prodpilot.server import run_stdio

app = typer.Typer(
    name="prodpilot",
    help="ProdPilot MCP server.",
    no_args_is_help=True,
    add_completion=False,
)


def configure_logging() -> None:
    """Route all log output to stderr.

    The stdio transport carries JSON-RPC frames on stdout. Anything else written
    to stdout corrupts the protocol stream and the client drops the connection,
    so stderr is the only safe destination for logs.
    """
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@app.callback()
def main() -> None:
    """ProdPilot MCP server.

    Declaring a callback keeps Typer in command group mode. Without it Typer
    collapses a single command into the root command and `prodpilot serve`
    fails with an unexpected argument error.
    """


@app.command()
def serve() -> None:
    """Serve the ProdPilot MCP server over stdio."""
    configure_logging()
    run_stdio()


if __name__ == "__main__":
    app()

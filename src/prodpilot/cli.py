"""Command line entry point for ProdPilot.

Exposes the MCP server as a launchable command so an MCP client can start it as
a subprocess. Section 9 of the Complete Solution Document specifies Typer for
the CLI.

Also carries the operator facing commands from module 1.3, setup and doctor,
which manage credentials and report on prerequisites.
"""

from __future__ import annotations

import logging
import sys

import typer

from prodpilot import config as config_module
from prodpilot import projectstate
from prodpilot.config import (
    ConfigError,
    Credentials,
    PermissionState,
    load_credentials,
    save_credentials,
)
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


def _read_secret(label: str) -> str:
    """Read one secret without echoing it.

    On a terminal this hides the input. When stdin is a pipe there is no
    terminal to hide from, and the hidden prompt cannot be used at all: on
    Windows getpass reads the console directly through msvcrt and ignores piped
    stdin, so the command would block forever. Reading the line directly keeps
    setup scriptable, and nothing is echoed either way.
    """
    if sys.stdin.isatty():
        return typer.prompt(label, hide_input=True, default="", show_default=False)

    typer.echo(f"{label}: ", nl=False)
    line = sys.stdin.readline()
    if not line:
        return ""
    return line.rstrip("\n").rstrip("\r")


def _prompt_secret(label: str, existing: str | None) -> str | None:
    """Prompt for one credential without echoing it.

    Returns None when the value is already stored and the developer accepts it
    by pressing enter. An empty answer with nothing stored is refused, since a
    blank credential would fail later at a much less obvious point.
    """
    if existing:
        typer.echo(f"{label} is already stored. Press enter to keep it.")
    while True:
        entered = _read_secret(label).strip()
        if entered:
            return entered
        if existing:
            return None
        typer.secho(f"{label} cannot be empty.", fg=typer.colors.RED, err=True)
        if not sys.stdin.isatty():
            # No one is there to retry. Fail rather than spin.
            raise typer.Exit(code=1)


@app.command()
def setup() -> None:
    """Store the GitHub token and Render API key for this developer.

    Values are written to ~/.prodpilot/config.toml, never inside a project.
    Nothing entered here is printed back to the terminal.
    """
    configure_logging()
    typer.echo("ProdPilot credential setup")
    typer.echo(f"Credentials are stored in {config_module.config_path()}")
    typer.echo("They are never written into a project directory.")
    typer.echo("")

    try:
        existing = load_credentials()
    except ConfigError as exc:
        typer.secho(f"Cannot read existing config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    github = _prompt_secret("GitHub token", existing.github_token)
    render = _prompt_secret("Render API key", existing.render_api_key)

    try:
        report = save_credentials(
            Credentials(
                github_token=github or existing.github_token,
                render_api_key=render or existing.render_api_key,
            )
        )
    except ConfigError as exc:
        typer.secho(f"Cannot save credentials: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.echo("")
    typer.secho(f"Saved to {config_module.config_path()}", fg=typer.colors.GREEN)

    if report.state is PermissionState.RESTRICTED:
        typer.secho(f"Permissions: {report.detail}", fg=typer.colors.GREEN)
        return

    # Loud on purpose. A credential file other accounts can read is a real
    # problem, and staying quiet about it would be worse than failing.
    colour = (
        typer.colors.RED
        if report.state is PermissionState.UNRESTRICTED
        else typer.colors.YELLOW
    )
    typer.secho(f"Permissions: {report.detail}", fg=colour, err=True)
    typer.secho(
        "Restrict this file before storing real credentials.", fg=colour, err=True
    )
    raise typer.Exit(code=1)


@app.command()
def doctor(
    project: str = typer.Option(
        None,
        "--project",
        help="Also check the project state conventions for this directory.",
    ),
) -> None:
    """Report whether ProdPilot's prerequisites are in place.

    Exits non-zero when something required is missing, so the failure is
    visible to a script rather than buried in output.
    """
    configure_logging()
    problems: list[str] = []
    path = config_module.config_path()

    typer.echo("ProdPilot doctor")
    typer.echo("")
    typer.echo(f"Config file: {path}")

    if not path.is_file():
        typer.secho("  not found. Run `prodpilot setup`.", fg=typer.colors.RED)
        problems.append("config file missing")
    else:
        typer.secho("  found", fg=typer.colors.GREEN)
        try:
            credentials = load_credentials()
        except ConfigError as exc:
            typer.secho(f"  unreadable: {exc}", fg=typer.colors.RED)
            problems.append("config file unreadable")
            credentials = Credentials()

        for key in config_module.REQUIRED_CREDENTIALS:
            present = bool(getattr(credentials, key))
            # Presence only. The value itself is never rendered.
            if present:
                typer.secho(f"  {key}: stored", fg=typer.colors.GREEN)
            else:
                typer.secho(f"  {key}: missing", fg=typer.colors.RED)
                problems.append(f"{key} missing")

        report = config_module.verify_permissions(path)
        colour = {
            PermissionState.RESTRICTED: typer.colors.GREEN,
            PermissionState.UNRESTRICTED: typer.colors.RED,
            PermissionState.UNKNOWN: typer.colors.YELLOW,
        }[report.state]
        typer.secho(f"  permissions: {report.detail}", fg=colour)
        if report.state is PermissionState.UNRESTRICTED:
            problems.append("credential file is readable by other accounts")

    if project:
        typer.echo("")
        typer.echo(f"Project: {project}")
        try:
            status = projectstate.check_gitignored(project)
            state = projectstate.read_project_state(project)
        except projectstate.ProjectStateError as exc:
            typer.secho(f"  {exc}", fg=typer.colors.RED)
            problems.append("project state unreadable")
        else:
            marker = projectstate.PROJECT_STATE_FILENAME
            if status.is_ignored:
                typer.secho(f"  {marker}: {status.detail}", fg=typer.colors.GREEN)
            else:
                typer.secho(f"  {marker}: {status.detail}", fg=typer.colors.RED)
                problems.append(f"{marker} is not gitignored")
            typer.echo(f"  stored state keys: {len(state)}")

    typer.echo("")
    if problems:
        typer.secho(
            f"{len(problems)} problem(s): " + "; ".join(problems),
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    typer.secho("All checks passed.", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()

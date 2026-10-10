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
from prodpilot import connect as clients
from prodpilot import prereqs
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


def print_version(value: bool) -> None:
    if value:
        from prodpilot import __version__

        typer.echo(f"prodpilot {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", is_eager=True, callback=print_version,
                                 help="Print the installed version and exit."),
) -> None:
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
    typer.echo("Tools ProdPilot runs:")
    for need in prereqs.check():
        typer.secho(f"  {need.name}: {need.detail}",
                    fg=typer.colors.GREEN if need.ok else typer.colors.RED)
        if not need.ok:
            problems.append(f"{need.name} missing")

    typer.echo("")
    if problems:
        typer.secho(
            f"{len(problems)} problem(s): " + "; ".join(problems),
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    typer.secho("All checks passed.", fg=typer.colors.GREEN)


AFTER = {
    "vscode": "In VS Code, run MCP: List Servers and start prodpilot.",
    "cursor": "In Cursor, open Settings, MCP, and switch prodpilot on.",
    "devin": "Reconnect prodpilot in Devin's MCP panel, or restart Devin, so it reads the file.",
}


@app.command()
def connect(
    client: str = typer.Argument(..., help="The IDE to connect: vscode, cursor or devin."),
    project: str = typer.Option(
        ".",
        "--project",
        help="Devin only: the project folder you open in Devin. Defaults to the current directory.",
    ),
) -> None:
    """Connect an IDE's agent to ProdPilot, naming this machine's prodpilot command.

    VS Code and Cursor are connected once for every project; Devin, which blanks
    the ${workspaceFolder} other configurations use, once per project.
    """
    if client not in clients.CLIENTS:
        typer.secho(f"Unknown client {client}. Choose one of: {', '.join(clients.CLIENTS)}.",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    try:
        if client == "vscode":
            done = clients.vscode()
        elif client == "cursor":
            done = clients.cursor()
        else:
            done = clients.devin(project)
    except clients.ConnectError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.secho(f"Added prodpilot to {done.where}", fg=typer.colors.GREEN)
    typer.echo(f"It starts: {done.command} serve")
    if done.ignored is False:
        typer.secho(
            f"{clients.DEVIN_FILE.as_posix()} names a path valid on this machine only "
            "and Git does not ignore it. Add it to .gitignore.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    typer.echo(AFTER[client])


@app.command()
def audit(
    project: str = typer.Argument(".", help="The project to audit. Defaults to the current directory."),
    ci: bool = typer.Option(False, "--ci",
                            help="Machine-readable JSON summary on stdout, no prompts, no network."),
    min_score: int = typer.Option(None, "--min-score",
                                  help="Fail when the score is below this number, 0 to 100."),
    fail_on: str = typer.Option("p0", "--fail-on",
                                help="Fail when a rule at this priority or above fails: p0, p1 or none."),
    baseline: str = typer.Option(None, "--baseline",
                                 help="Also fail when the project regressed against this Git ref."),
) -> None:
    """Audit a project read-only and check it is ready.

    Exit codes: 0 when the readiness check passed, 1 when it failed, 2 for a
    tool or usage error such as a path that is not a project or a baseline ref
    that cannot be resolved.
    """
    import json

    from prodpilot import ci as checks

    configure_logging()
    try:
        outcome = checks.check(project, fail_on=fail_on, min_score=min_score,
                               baseline=baseline)
    except (checks.CIError, NotADirectoryError, OSError) as exc:
        if ci:
            typer.echo(json.dumps({"passed": False, "exit_code": checks.ERROR,
                                   "error": str(exc)}))
        typer.secho(f"prodpilot audit: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=checks.ERROR)
    if ci:
        typer.echo(json.dumps(outcome.to_dict(), indent=2))
    else:
        typer.echo(outcome.summary())
    raise typer.Exit(code=outcome.code)


@app.command()
def fix(
    project: str = typer.Argument(".", help="The project to preview. Defaults to the current directory."),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="Show the changes the fix loop would make, and write nothing."),
    rule: str = typer.Option(None, "--rule", help="Preview only this rule, for example SEC-002."),
) -> None:
    """Preview the fix loop's changes without writing anything.

    Content contracts are shown as unified diffs, in the order the loop would
    reach them. A refused parametric rule shows its reason and candidates, and a
    delegated rule shows the contract your IDE agent works within. The fixes
    themselves are applied by your IDE's agent through prodpilot_fix_instruction.
    """
    from prodpilot import preview

    configure_logging()
    if not dry_run:
        typer.secho("prodpilot fix only previews, so pass --dry-run. Your IDE's agent applies "
                    "fixes through prodpilot_fix_instruction.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    try:
        planned = preview.plan(project, rule=rule)
    except (preview.PreviewError, NotADirectoryError, OSError) as exc:
        typer.secho(f"prodpilot fix: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    for item in planned:
        typer.echo(item.text())
        typer.echo("")
    diffs = sum(1 for p in planned if p.kind == preview.DIFF)
    typer.echo(f"{len(planned)} rule(s) previewed, {diffs} with a diff. Nothing was written.")


@app.command()
def deploy(
    project: str = typer.Argument(".", help="The project to deploy. Defaults to the current directory."),
    branch: str = typer.Option("main", "--branch", help="The branch pushed to and deployed from."),
    rollback: bool = typer.Option(
        True, "--rollback/--no-rollback",
        help="Roll a redeployed service back to its previous live deploy when the "
             "smoke test fails. On by default."),
) -> None:
    """Run the nine deploy stages, as the prodpilot_deploy tool does.

    This creates or redeploys a real Render service and pushes to the project's
    GitHub repository. Exits 0 when every stage passed and 1 when a stage
    stopped the run, naming the stage and why.
    """
    from prodpilot import prodpush

    configure_logging()
    shipped = prodpush.run(project, branch=branch, rollback=rollback)
    for step in shipped.steps:
        if step.ok:
            state = "pass" if step.ran else "skipped"
        else:
            state = "FAIL" if step.ran else "not reached"
        detail = step.detail if step.ok or step.ran else ""
        typer.echo(f"  {step.stage.value:<24} {state:<11} {detail}".rstrip())
    typer.echo(shipped.summary())
    raise typer.Exit(code=0 if shipped.ok else 1)


# Report formats and the function that renders each from a reporting.View.
FORMATS = ("sarif", "md", "html")


def render_report(fmt: str, view) -> str:
    """The report text for one format."""
    import json

    from prodpilot import formats
    from prodpilot.sarif import to_sarif

    if fmt == "sarif":
        return json.dumps(to_sarif(view), indent=2, ensure_ascii=False) + "\n"
    if fmt == "md":
        return formats.to_markdown(view, formats.decide(view))
    if fmt == "html":
        return formats.to_html(view, formats.decide(view))
    raise ValueError(f"unknown report format {fmt}")


@app.command()
def report(
    project: str = typer.Argument(".", help="The project to audit. Defaults to the current directory."),
    fmt: str = typer.Option(..., "--format", help="Report format: " + ", ".join(FORMATS) + "."),
    out: str = typer.Option(..., "--out", help="File to write the report to."),
) -> None:
    """Audit a project read-only and write a report.

    Exits 0 when the report was written, whatever the audit found. Exits 2 when
    the report could not be produced: an unknown format, a path that is not a
    project, or a file that could not be written.
    """
    configure_logging()
    from pathlib import Path

    from prodpilot import reporting

    if fmt not in FORMATS:
        typer.secho(f"Unknown format {fmt}. Choose one of: {', '.join(FORMATS)}.",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    try:
        view = reporting.build(project)
    except (NotADirectoryError, OSError, ValueError) as exc:
        typer.secho(f"Cannot audit {project}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    text = render_report(fmt, view)
    target = Path(out)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except OSError as exc:
        typer.secho(f"Cannot write {target}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    typer.echo(f"Wrote {fmt} report for {view.project} to {target}: score "
               f"{view.report.score}/100, {len(view.items)} failing rule(s).")


if __name__ == "__main__":
    app()

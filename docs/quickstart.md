# Quick start

ProdPilot is an MCP server your IDE's coding agent uses to audit a Node.js with
Express or React with Vite project, fix what would stop it running in
production, and deploy it to Render with a CI/CD pipeline. It runs on your
machine and calls no AI service itself: your IDE's agent does the talking, and
ProdPilot checks every fix the agent makes against the rule's own checker.

## What you need

- Python 3.11, 3.12, 3.13 or 3.14
- Node.js 20 or later, which the audit uses to parse JavaScript
- Docker, running, because the gate and ProdPush build your project in a
  container before anything is deployed
- git, and a GitHub repository for your project, set as its `origin`
- A GitHub token that can push, set repository secrets and add workflow files:
  a classic token with the `repo` and `workflow` scopes, or a fine grained
  token with read and write access to contents, secrets and workflows, and read
  access to actions
- A Render account and a Render API key
- VS Code with GitHub Copilot, Cursor, or Devin (formerly Windsurf)

## Install

```bash
pipx install prodpilot        # or: uv tool install prodpilot
```

Until the first release is on PyPI, install from a clone of the repository with
`pip install .`.

## Set up

```bash
prodpilot setup     # stores the GitHub token and the Render API key
prodpilot doctor    # checks git, Node.js, Docker and the credentials
```

`setup` writes both values to `~/.prodpilot/config.toml`, restricts the file to
your account and checks that it did. Nothing you type is printed back. Fix
anything `doctor` reports before going on.

## Connect your IDE

```bash
prodpilot connect vscode                  # once, for every project
prodpilot connect cursor                  # once, for every project
prodpilot connect devin --project PATH    # once per project
```

Then:

- VS Code: run **MCP: List Servers** from the Command Palette and start
  `prodpilot`. The first call to each tool asks for your approval.
- Cursor: open **Settings**, **MCP**, and switch `prodpilot` on. On a fresh
  install Cursor runs tools without asking; turn on approval for tool calls if
  you want to confirm every deploy.
- Devin: reconnect `prodpilot` in the MCP panel. Devin asks before every call.

## Use it

Open your project and ask the agent, for example:

> Use ProdPilot to audit this project and fix what it reports, one rule at a
> time, reporting each fix with prodpilot_fix_applied.

The agent asks ProdPilot for a fix contract, applies it, and reports it;
ProdPilot then runs the rule's own checker and answers whether the fix really
worked. A fix that breaks a rule which passed before is undone. When the
project is ready, commit your changes and ask:

> Deploy this project with prodpilot_deploy.

The deploy tool creates a real Render service on the free plan and pushes to
your GitHub repository, so the agent is told to confirm with you first. It runs
nine stages and stops at the first that fails, saying which and why:

1. The scoring gate: an audit score of at least 90, no critical rule failing,
   and the deployability model's estimate at or above its operating point.
2. Pre-flight: a Git repository with a GitHub origin, and both credentials.
3. Environment sealing: your `.env` values, placeholders refused.
4. A Docker build of the project, run and probed locally.
5. A push of the files ProdPilot generated, never your own uncommitted work.
6. The Render deployment.
7. Deploy monitoring until the service is live.
8. A smoke test of the live service: health, an API route, security headers,
   CORS and no stack traces.
9. CI/CD wiring: a GitHub Actions workflow that redeploys on every push and
   checks the service afterwards.

## What leaves your machine

ProdPilot talks to GitHub (pushes of the files it generated, repository
secrets encrypted on your machine, workflow status), to Render (the service,
your sealed environment values, deploy status and logs), and, while building
your project, to Docker Hub and the npm registry. It sends no telemetry and
calls no AI service.

## Limits

- Supported stacks: Node.js with Express, and React with Vite.
- Deployment target: Render only.
- Some fixes are written by your agent within a boundary ProdPilot sets.
  ProdPilot verifies each one, but how often a live agent's change passes on
  the first try has not been measured yet.
- A project whose code requires a file it does not contain can pass the audit;
  the Docker build in stage 4 of the deploy catches it and names the missing
  module.

## Troubleshooting

- Run `prodpilot doctor` first. It names anything missing.
- VS Code: the Copilot agent's first connection can be refused with protocol
  error -32022 and connects on its automatic retry. If it does not, restart
  `prodpilot` from **MCP: List Servers**.
- Devin cannot start ProdPilot from VS Code or Cursor configuration files; use
  `prodpilot connect devin --project PATH`.
- The gate refuses a project: the reason says which condition failed. A React
  project that cannot build, for example one without an `index.html`, gets a
  very low estimate from the model.
- The Docker build fails: the stage names the first line of the build output
  that explains why, such as `npm ci` refusing to run without a
  `package-lock.json`.

Report a security problem privately as described in
[SECURITY.md](../SECURITY.md), and anything else as a GitHub issue.

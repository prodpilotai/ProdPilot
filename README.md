# ProdPilot

An AI-Powered Production Readiness and Automated Deployment System for Vibe-Coded Projects

ProdPilot is a local MCP server that gives an IDE's existing coding agent a set of
deployment-focused tools. It runs no model of its own and calls no external AI API.

Final Year Project, BSAI-FYP-2026, Department of Artificial Intelligence,
Shifa Tameer-e-Millat University.

## Current state

Phase 1 is implemented: the MCP server skeleton, Layer 0 detection and
blueprints, and local config and secrets handling. The audit engine, the fix
loop, the scoring gate, and the deployment module are not built yet.

## Running the server

ProdPilot speaks the Model Context Protocol over stdio. An MCP client starts it
as a subprocess.

```
prodpilot serve
```

Registering it in VS Code is done through `.vscode/mcp.json` in this repository.

## Tools

| Tool | Purpose |
| --- | --- |
| `prodpilot_ping` | Connectivity check. Returns a fixed payload, reads no files. |
| `prodpilot_detect_stack` | Identifies a project's stack and returns the matching production blueprint. |

Commands: `prodpilot serve`, `prodpilot setup`, `prodpilot doctor`.

## Layer 0: detection and blueprint

Layer 0 answers two questions about a target project: what stack is this, and
what would a deployment-ready version of it have to contain.

### Stack detection

Detection reads the project's file structure and `package.json`. It is fully
deterministic, involves no model, and makes no network call.

| Stack | Matched when |
| --- | --- |
| `node_express` | `express` is a declared dependency |
| `react_vite` | `react` and `react-dom` are declared, and Vite is present |
| `unrecognized` | anything else |

Vite counts as present if the `vite` package is declared or a `vite.config`
file exists at the project root, in any of the `.js`, `.mjs`, `.cjs`, `.ts`,
`.mts` or `.cts` forms. Vite is often only a transitive install, so the config
file is treated as equally strong evidence.

Section 9 of the Complete Solution Document freezes v1 at these two stacks.
Django and everything else is unrecognized by design, not by omission.

Detection fails closed. A project declaring both Express and React with Vite
returns `unrecognized` with an explanation rather than a guess, because the two
blueprints describe substantially different production shapes and picking the
wrong one would be worse than picking none.

A missing or unparseable `package.json` is a property of the project under
inspection, so it resolves to `unrecognized`. Only a path that does not exist or
is not a directory raises `DetectionError`.

### Production blueprints

A blueprint declares every file, config, and code pattern a deployment-ready
project of that stack must have. It is data only. It does not check anything and
it does not fix anything. Rule checks are Phase 2 and fix generation is Phase 3.

Each requirement carries a stable `item_id`, a `domain`, and a `priority`, using
the domain and priority vocabulary from Section 4 of the Complete Solution
Document so Phase 2 can map blueprint items onto rule definitions without
renaming anything.

| Blueprint | Requirements |
| --- | --- |
| Node.js with Express | 28 |
| React with Vite | 22 |

Item ids are namespaced by stack, `node.` and `react.`, so the two sets never
collide.

Every one of the nine Section 4 domains is either covered by at least one
requirement or listed in `not_applicable_domains` for that stack. A static
single page application opens no database connection and serves no API of its
own, so `connectivity` and `api` are declared inapplicable to the React blueprint
rather than left silently absent. This distinction matters to Phase 2, which
otherwise cannot tell a domain that does not apply from one whose rules were
forgotten.

## Configuration and secrets

ProdPilot keeps two kinds of state, deliberately in two different places.

| | Location | Holds | Committed |
| --- | --- | --- | --- |
| Credentials | `~/.prodpilot/config.toml` | GitHub token, Render API key | never |
| Project state | `<project>/.env.prodpilot` | deployment identifiers for one project | never, gitignored |

A GitHub token and a Render API key belong to the developer, not to any one
repository, so they live in the user's home directory and never inside a project
tree. Deployment state belongs to a single project, so it lives beside it.

Design principle 5 in Section 2.1 of the Complete Solution Document states that
secrets are never written to any committed file. Both halves of this split exist
to uphold that.

### First run

```
prodpilot setup
```

Prompts for the GitHub token and the Render API key, writes them to
`~/.prodpilot/config.toml`, and restricts that file to the current user. Nothing
entered is printed back to the terminal, and rerunning it lets you keep an
existing value by pressing enter.

File access is restricted and then verified rather than assumed. On POSIX the
file is set to mode `0600`. On Windows `os.chmod` cannot express this, since it
only toggles the read only flag and leaves inherited entries for other accounts
in place, so ProdPilot breaks inheritance and grants the current user sole
access through `icacls`. Either way the resulting permissions are read back and
reported. If the file could not be restricted, setup says so and exits non-zero
rather than reporting success it cannot prove.

### Checking prerequisites

```
prodpilot doctor
prodpilot doctor --project /path/to/project
```

Reports whether the config file exists, whether each required credential is
stored, and whether the file is readable by other accounts. Credential values
are never rendered, only their presence. With `--project` it also reports
whether `.env.prodpilot` is excluded from version control. It exits non-zero
when anything required is missing, so a failure is visible to a script.

### `.env.prodpilot`

A project local, gitignored file holding per project deployment state. ProdPush
populates it in Phase 6. The keys are fixed now so nothing has to invent them
later:

| Key | Meaning |
| --- | --- |
| `PRODPILOT_SERVICE_ID` | Render service identifier |
| `PRODPILOT_DEPLOY_ID` | most recent deploy identifier |
| `PRODPILOT_SERVICE_URL` | live service URL |

Writing a key whose name looks like a credential, matching `TOKEN`, `SECRET`,
`PASSWORD`, `API_KEY`, `PRIVATE_KEY` or `CREDENTIAL`, is refused rather than
warned about. There is no supported way to put a secret into a project
directory through this API.

## Development

```
pip install -e ".[dev]"
pytest
```

Sample projects used by the detection tests live under `tests/samples/`. They are
minimal but genuine, each with a real `package.json` and entry point.

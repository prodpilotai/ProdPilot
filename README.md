# ProdPilot

An AI-Powered Production Readiness and Automated Deployment System for Vibe-Coded Projects

ProdPilot is a local MCP server that gives an IDE's existing coding agent a set of
deployment-focused tools. It runs no model of its own and calls no external AI API.

Final Year Project, BSAI-FYP-2026, Department of Artificial Intelligence,
Shifa Tameer-e-Millat University.

## Current state

Phases 1 to 6 are complete. A Node.js with Express or React with Vite project
can be audited, fixed through the IDE agent, gated, and deployed to Render with
an active CI/CD pipeline.

| Phase | What it built |
| --- | --- |
| 1 | The MCP server, Layer 0 stack detection and production blueprints, local config and secrets |
| 2 | The audit engine: 50 rules, 28 for Node Express and 22 for React Vite, across nine domains, scored 0 to 100 |
| 3 | The bounded fix loop: fix contracts for every rule, an independent verifier, and a guard against one fix breaking another |
| 4 | The scoring gate, looping the fix cycle until the project is ready or the ceiling is reached |
| 5 | The deployability model, trained on 684 real Render deployments |
| 6 | ProdPush, the nine stage pipeline from the gate to a live, smoke tested service |

Phase 1 closed with one stated gap. Invocation from Copilot agent mode is
untested, blocked by an exhausted account quota rather than by anything in the
project, and the two Section 8 week-one questions move to Phase 7. See
[docs/phase1-verification.md](docs/phase1-verification.md) for the full record.

Phase 7, IDE compatibility, is in progress. Every tool is proven over the
commands in `.vscode/mcp.json` and `.cursor/mcp.json`, and each client's
results, including failures and anything not yet tested and why, are in
[docs/compatibility.md](docs/compatibility.md).

Numbers quoted in some earlier commit messages describe a model that has since
been corrected and replaced. See [docs/corrections.md](docs/corrections.md).

## Running the server

ProdPilot speaks the Model Context Protocol over stdio. An MCP client starts it
as a subprocess.

```bash
prodpilot serve
```

Registering it in VS Code is done through `.vscode/mcp.json` in this repository.

## Tools

| Tool | Purpose |
| --- | --- |
| `prodpilot_ping` | Connectivity check. Returns a fixed payload, reads no files. |
| `prodpilot_detect_stack` | Identifies a project's stack and returns the matching production blueprint. |
| `prodpilot_fix_instruction` | Returns the fix contract for one failing rule. |
| `prodpilot_fix_applied` | Takes the agent's report of an applied fix and answers from the rule's own checker, never from the report. |
| `prodpilot_deploy` | Runs the whole ProdPush pipeline. It creates a real Render service and pushes to GitHub, so its description asks the agent to confirm with the developer first. |

Commands: `prodpilot serve`, `prodpilot setup`, `prodpilot doctor`.

## From audit to production

**Audit.** Every rule belongs to one of nine domains (security, secrets,
environment, build, connectivity, api, structure, observability, git hygiene)
and one priority from P0, critical, to P5. The score maps to a band: 0 to 39 Not
Ready, 40 to 69 Needs Work, 70 to 89 Nearly Ready, 90 to 100 Production Ready.

**Fix loop.** Each rule has a fix type. 28 are static, a fixed change; 16 are
parametric, a change built from values read out of the project; 6 are
delegated, where ProdPilot supplies a boundary and the agent writes the change.
A fix counts only when the rule's own checker passes again on the files on
disk. Each rule gets three attempts, and a fix that breaks a rule which passed
before it is reverted and sent to manual review.

**Gate.** The project may deploy only when no critical rule fails, the audit
score is at least 90, and the model's estimate that it will really deploy is at
or above the model's operating point. The fix loop runs again while the gate
stays shut, up to five cycles.

**ProdPush.** The gate, pre-flight checks, environment sealing, a local Docker
build test, a push of the generated files, the Render deployment, deploy
monitoring, a post-deploy smoke test, and CI/CD wiring through GitHub Actions.
It stops at the first stage that fails and says which one and why.

## The deployability model

The model estimates whether a project will really deploy on Render and serve
its health path. It learned from 684 projects deployed to Render for real, 129
of which did.

Its 26 features are 25 counts derived from the audit, and whether the project
builds. The build result comes from `prodpilot.builds`, which runs Render's own
build command in a Linux container, from a read only copy of the project, with
the Node version Render would choose. The gate runs the same check on the
project it is judging whenever it needs the model's estimate.

The estimator is a HistGradientBoostingClassifier with sigmoid calibration and a
monotonic constraint: clearing a failing rule, or a build starting to succeed,
can never lower the estimate. Training checks this on every row and refuses a
model that breaks it. On held out projects it scores a ROC AUC of 0.909, 0.852
within React and 0.913 within Express. The full evaluation, and how the gate
policy was decided from it, is in [docs/evaluation.md](docs/evaluation.md).

The trained model is `data/model.joblib`, which is not committed. Without it the
gate gives no estimate and stays shut; it never falls back to the audit score.

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

```bash
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

```bash
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
writes it when it deploys the project:

| Key | Meaning |
| --- | --- |
| `PRODPILOT_SERVICE_ID` | Render service identifier |
| `PRODPILOT_DEPLOY_ID` | most recent deploy identifier |
| `PRODPILOT_SERVICE_URL` | live service URL |

Writing a key whose name looks like a credential, matching `TOKEN`, `SECRET`,
`PASSWORD`, `API_KEY`, `PRIVATE_KEY` or `CREDENTIAL`, is refused rather than
warned about. There is no supported way to put a secret into a project
directory through this API.

## Data

Two local directories are never committed: `data/` holds the Phase 5 dataset
and the model in use, and `backup/` holds files that were replaced or retired.

| `data/` | Holds |
| --- | --- |
| `repos.jsonl` | The collected repositories, each pinned to a commit |
| `cache/` | Those repositories, cloned at their pinned commits |
| `negatives.jsonl`, `negatives/` | Synthetic negatives, real projects with one rule deliberately broken |
| `mirror.json` | Where each synthetic negative was published for Render to deploy |
| `labels.jsonl` | The real Render outcome of each of the 684 deployed projects |
| `labels.excluded.jsonl` | The project that could not be deployed, and why |
| `labelling.log` | The record of the labelling run |
| `builds.jsonl` | Whether each labelled project builds, with its Node version and log |
| `features.jsonl`, `features.names.json` | The 26 feature matrix, 675 rows, and its column names |
| `features.excluded.jsonl` | The 10 rows left out of the matrix, 9 whose build could not be determined and 1 never labelled, and why |
| `features.skipped.jsonl` | The repository the audit could not process |
| `model.joblib` | The model the gate uses |
| `evaluation.json` | That model's measurements, and the comparison it was chosen from |
| `training.log` | The output of the training run that produced it |

`backup/` keeps superseded files rather than deleting them: earlier label and
feature snapshots under `backup/data-history/`, and earlier model candidates,
logs and trial files under `backup/data-retired-20260913/`. A full copy of
`data/` taken before the build feature was added is kept outside the
repository.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Sample projects used by the tests live under `tests/samples/`. They are minimal
but genuine, each with a real `package.json` and entry point.

The tests reach no live service: every Render and GitHub call is scripted, and
the build check's tests script Docker too. Tests that build a real Docker image
with the local daemon are skipped when none is available, and `tests/conftest.py`
runs the suite on one OpenMP thread, which is faster for the small models the
training tests fit.

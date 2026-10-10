# ProdPilot in CI

ProdPilot can check a project on every push and pull request, either through
its GitHub Action or with the `prodpilot audit` command in any CI system. Both
only read the project. Nothing is fixed, built for deployment or deployed.

## The GitHub Action

```yaml
name: Production readiness

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read
  security-events: write

jobs:
  prodpilot:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          # The pull request's base commit must be available for the baseline
          # comparison, so fetch the full history.
          fetch-depth: 0

      - uses: prodpilotai/ProdPilot@v1
        with:
          path: .
          fail-on: p0
          min-score: "70"
```

### Inputs

| Input | Default | What it does |
| --- | --- | --- |
| `version` | this release | The ProdPilot version installed from PyPI, pinned exactly. |
| `path` | `.` | The project to audit, relative to the repository root. |
| `min-score` | none | Fail when the score is below this number. |
| `fail-on` | `p0` | Fail when a rule at this priority or above fails: `p0`, `p1` or `none`. |
| `baseline` | the pull request's base commit | Also fail when the project regressed against this ref. Empty on push events, so no comparison runs there. |
| `sarif` | `true` | Upload the findings to GitHub code scanning. |

The action has one output, `exit-code`: 0, 1 or 2, as below.

### What it does

1. Sets up Python and installs `prodpilot==<version>`.
2. Writes a SARIF report and uploads it with GitHub's code scanning upload
   action, pinned to a full commit.
3. Writes the Markdown report to the job summary.
4. Runs `prodpilot audit --ci` with the inputs, which decides whether the job
   passes.

It needs no secret. The workflow needs only `contents: read` and
`security-events: write`.

### When code scanning is not available

SARIF upload needs code scanning to be available for the repository: on public
repositories it is, and on private ones it needs GitHub Advanced Security. A
pull request from a fork also cannot write security events. In those cases the
upload step fails, the action adds a notice saying the upload was skipped, and
the job is still decided by the readiness check alone. Set `sarif: "false"` to
skip the upload entirely.

### Where findings point

Paths in the SARIF report are relative to the repository root. A finding the
checker tied to a line points at that line; the checkers record lines, not
columns. A file the project is missing, and a rule about the project as a whole,
are attached to the project's `package.json`, and the message names the missing
file.

## prodpilot audit

```
prodpilot audit [PROJECT] [--ci] [--min-score N] [--fail-on p0|p1|none] [--baseline REF]
```

| Flag | What it does |
| --- | --- |
| `--ci` | Prints a JSON summary on stdout. Never prompts and uses no network. |
| `--min-score N` | Fails when the score is below N. |
| `--fail-on` | Fails when a rule at this priority or above fails. Default `p0`. |
| `--baseline REF` | Audits the project at REF in a temporary Git worktree outside the project, removed afterwards, and fails when the score dropped or any rule that passed at REF now fails, even if the score rose. |

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | The readiness check passed. |
| 1 | The readiness check failed: a rule at or above `--fail-on` fails, the score is under `--min-score`, the project regressed against `--baseline`, or its stack is not supported. |
| 2 | A tool or usage error: the path is not a project, a flag value is invalid, Git is missing, or the baseline ref cannot be resolved. |

A shallow clone does not hold the baseline commit. The error says so and names
the fix: `fetch-depth: 0` on `actions/checkout`.

## prodpilot report

```
prodpilot report --format sarif|md|html --out PATH [PROJECT]
```

Audits the project read-only and writes the report. It exits 0 whenever the
report was written, whatever the audit found, and 2 when it could not be
produced. A credential is never printed in any format: secret findings show the
file, the line and the first four characters with the length.

# Changelog

## 1.1.0, unreleased

A hardening release. It adds tooling around ProdPilot and changes nothing about
how a project is judged: the ruleset, the scoring weights and bands, the shipped
model and its scikit-learn pin, and the gate's three conditions are unchanged
from 1.0.0, and tests now hold each of them to its 1.0.0 value.

### Added

- `prodpilot audit` for CI, with `--ci`, `--min-score`, `--fail-on p0|p1|none`
  and `--baseline <git-ref>`. It exits 0 when the project passes, 1 when it does
  not and 2 for a tool or usage error. The baseline is audited in a temporary
  worktree that is always removed, and a rule that regressed fails the check
  even when the score rose. See [docs/github-action.md](docs/github-action.md).
- `prodpilot report --format sarif|md|html`. The SARIF 2.1.0 log describes all
  50 rules, has stable fingerprints for GitHub code scanning and is validated
  against the official schema in the tests. The HTML is one self-contained file.
  No format prints a credential.
- A GitHub Action, `prodpilotai/ProdPilot@v1`, that uploads the SARIF report to
  code scanning, writes the Markdown report to the job summary and fails the job
  when the project is not ready. It needs no secret, and a self-test workflow
  runs it on Linux, macOS and Windows.
- `prodpilot fix --dry-run [--rule ID]`, a preview of the fix loop's changes as
  unified diffs in loop order, and an optional `dry_run` input on
  `prodpilot_fix_instruction`. See [docs/dry-run.md](docs/dry-run.md).
- `prodpilot deploy [--no-rollback]`, which runs the nine deploy stages from a
  terminal, and an optional `rollback` input on `prodpilot_deploy`.
- Rollback after a failed smoke test: a redeployed service is rolled back to its
  previous live deploy of a different commit, monitored and health checked
  again. See [docs/rollback.md](docs/rollback.md).
- The declared Node version, read where Render reads it, in the detection
  tool's blueprint, and an end-of-life notice in the audit and every report that
  never changes the score or the gate. See
  [docs/node-versions.md](docs/node-versions.md).
- Guard tests for the frozen core, the 1.0.0 interface and the behaviour of all
  22 sample projects.

### Changed

- Rollback is on by default. `--no-rollback`, or `rollback: false` on the deploy
  tool, keeps 1.0.0's behaviour of leaving a failed deploy in place.
- A project already deployed by ProdPilot is redeployed to the service recorded
  in `.env.prodpilot` rather than given a second service.
- Generated Dockerfiles and CI workflows use the project's declared Node major
  when it is 20, 22, 24 or 26, and Node 24 otherwise, instead of Node 20, which
  reached end of life on 30 April 2026. The version is read in Render's order:
  `NODE_VERSION` in `.env`, `.node-version`, `.nvmrc`, then `engines.node`. A
  range spanning several majors, or with no upper bound, refuses and lists its
  candidates. Rule outcomes, scores and gate decisions are unchanged.
- The contract executor the tests used moved into the package as
  `prodpilot.applying`, so the dry-run preview applies changes the same way.

### Fixed

- A second deploy of a project stopped at the push stage when the only untracked
  file was ProdPilot's own `.env.prodpilot`, because git reported "nothing added
  to commit" in words the push stage did not recognise.
- The 1.0.0 package reported its version as 1.0.0rc1. The version now has one
  source, `prodpilot/__init__.py`.

## 1.0.0, 20 September 2026

The first stable release. What changed is what the project now promises, how
widely it is tested, and two faults that running the suite on Windows for the
first time uncovered.

### Fixed

- A Docker daemon in Windows container mode was reported as available, and
  every build then failed with "no matching manifest for windows/amd64" and no
  classification. ProdPilot now says the daemon runs Windows containers and
  that its images are Linux, which names what to change.
- On Windows, the credential file was reported as readable by other accounts
  when the only entries left were SYSTEM, the Administrators group and OWNER
  RIGHTS. The first two read every file on the machine whatever a file's
  entries say, and they survive breaking inheritance on an account that is an
  administrator, so the warning could not be acted on; they are now expected
  and named in the report. OWNER RIGHTS is not an account at all, but a bound
  on what the file's owner may do, so it is not counted. Any other account on
  the file is still a fault.

### Added

- [docs/stability.md](docs/stability.md): what 1.x keeps stable. The five tool
  names and their fields, the rule identifiers, the commands and their flags,
  where credentials live, and the gate's three conditions. It also says what is
  not promised, including the score a given project receives, the number of
  rules and the model's estimates.
- The test workflow runs on Windows as well as Linux and macOS, so the platform
  ProdPilot is developed on is covered by its own suite.
- The packaged wheel is installed outside the repository on all three systems,
  and the shipped model is loaded there, so what `pip install prodpilot` gives
  a user is checked on each of them.
- Files published to PyPI carry attestations, so anyone can check that they
  were built by this repository's release workflow.

### Known limits

These are unchanged from the release candidates and are stated here rather than
dropped:

- Supported stacks are Node.js with Express and React with Vite; the only
  deployment target is Render.
- How often a live agent's delegated fix passes verification has not been
  measured across the six delegated rules.
- Not yet observed end to end on a real service: the deploy tool called by an
  IDE agent, and the CI/CD workflow ProdPilot writes redeploying a live service
  from GitHub Actions. The nine stages and the generated workflow are covered
  by the suite against scripted APIs.
- Render's own automatic deploy stays on, so a push can start a second deploy
  beside the workflow's; the workflow waits for the newer one.

## 1.0.0rc2, 20 September 2026

A packaging and documentation release. The code is the same as 1.0.0rc1.

### Fixed

- The description on PyPI said the release was not on PyPI yet, because that
  was true when 1.0.0rc1 was built. A description can only be corrected by
  publishing again, which is what this version is for.

### Changed

- The install table names pip beside pipx and uv, and says why pipx and uv are
  recommended for a command line tool.
- Tagging now publishes on its own: the release workflow creates the GitHub
  release after the upload, with the notes this changelog already holds for the
  version, the wheel and the sdist attached, and a candidate marked as a
  pre-release. An upload skips files already on PyPI, so a re-run of a tag no
  longer fails.

## 1.0.0rc1, 20 September 2026

The first release candidate, prepared for a beta with outside users.

### Added

- The MCP server with five tools: stack detection with production blueprints,
  fix instructions and independent verification of applied fixes for fifty
  rules, and ProdPush, which takes a project that passes the scoring gate to a
  live, smoke tested Render service with an active CI/CD pipeline.
- `prodpilot connect vscode|cursor|devin`, which connects each IDE's agent to
  the installed ProdPilot in the place that IDE documents.
- `prodpilot doctor` now checks git, Node.js and the Docker daemon.
- Tool annotations: four tools are marked read only and the deploy tool as
  changing its environment.
- LICENSE, SECURITY.md, third-party notices, a test workflow and a release
  workflow for PyPI trusted publishing.

### Changed

- The gate's model ships inside the package, so an installed ProdPilot has
  one; scikit-learn is pinned to 1.9.0, the version that saved it.
- Runs on Python 3.11 to 3.14, where it was 3.11 only. The full suite passes
  on 3.11, 3.13 and 3.14 on Windows with a Docker daemon; 3.12 is covered by
  the test workflow.
- The CI/CD workflow ProdPilot writes waits for the deploy it started before
  checking the service, instead of a fixed 90 second wait.
- Content fixes declare the environment keys they read, so a CORS fix no
  longer breaks the environment template and gets reverted.

### Fixed

- A failed Docker build kept none of its output, because the build log was read
  twice from a one-pass generator; its summary now names the cause.

### Known limits

- Supported stacks are Node.js with Express and React with Vite; the only
  deployment target is Render.
- How often a live agent's delegated fix passes verification has not been
  measured.
- Not yet observed on real services: the deploy tool called by an IDE agent,
  the new workflow on GitHub Actions, and `prodpilot connect` in the real
  clients.
- Render's own automatic deploy stays on, so a push can start a second deploy
  beside the workflow's; the workflow waits for the newer one.

# Changelog

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
  when the only entries left were SYSTEM and the Administrators group. Those
  two read every file on the machine whatever a file's entries say, and they
  survive breaking inheritance on an account that is an administrator, so the
  warning could not be acted on. They are now expected, and named in the
  report; any other account on the file is still a fault.

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

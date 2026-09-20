# Changelog

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

# Implementation facts

Collected by `python tests/facts.py docs/revision` from the repository,
the data directory and a live stdio session with the server.

| Fact | Value |
| --- | --- |
| Version in pyproject.toml | 1.0.0 |
| Version the server reports | 1.0.0rc1 |
| The two agree | no |
| Tags | 1.0.0rc1 (a18d4a4), v1.0.0 (7b7693f), v1.0.0rc1 (6ab7382), v1.0.0rc2 (594a721) |
| Tests collected | 1828 |
| CI operating systems | ubuntu-latest, macos-latest, windows-latest |
| CI Python versions | 3.11, 3.12, 3.13, 3.14 |
| Workflow at the live deploy | 679b0a9 2026-09-12 Close Phase 6 with the ProdPush pipeline and the deploy tool, fixed wait: yes |
| Wait-for-deploy workflow | a8e059d 2026-09-15 Make the CI workflow wait for the deploy it started |
| Workflow run history | not measured: the GitHub CLI is not authenticated on this machine |
| Largest response, text content | 6431 bytes |
| Largest response, structured content | 5003 bytes |
| Largest response, whole result | 11988 bytes (11.71 KiB, 11.99 KB) |
| Blueprint items in that response | 28 |
| Node major in generated Dockerfiles | 20 |
| Images the build pass may use | node:lts, node:latest, node:22-slim, node:20, node:16, node:12, node:10 |
| Build pass rows | 684 |
| Rows that fell back to another image | 39 |

Build pass rows by image:

| Image | Rows |
| --- | --- |
| node:lts | 503 |
| node:latest | 128 |
| node:20 | 21 |
| node:22-slim | 19 |
| node:12 | 8 |
| node:16 | 3 |
| node:10 | 2 |

Rows that fell back, by the major they asked for:

| Wanted | Rows |
| --- | --- |
| 18 | 12 |
| 4 | 11 |
| 6 | 7 |
| 8 | 6 |
| 0 | 2 |
| 14 | 1 |

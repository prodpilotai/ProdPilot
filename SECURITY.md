# Security policy

ProdPilot handles a GitHub token and a Render API key and acts on real
infrastructure, so security reports are taken seriously.

## Reporting a vulnerability

Report it privately through GitHub: open the repository's **Security** tab and
choose **Report a vulnerability**. Do not open a public issue for a
vulnerability. Include what you did, what happened, and the version that
`pip show prodpilot` reports.

## Supported versions

| Version | Supported |
| --- | --- |
| 1.1.0 and later 1.1 releases | Yes |
| 1.0 releases | No: upgrade to 1.1, which changes no rule, score or gate decision |
| Anything earlier | No |

## How ProdPilot handles your credentials

- The GitHub token and the Render API key are stored in
  `~/.prodpilot/config.toml`, never inside a project. `prodpilot setup`
  restricts the file to the current user and verifies that it did; on Windows
  it removes inherited access through `icacls`.
- A project's deployment identifiers live in `.env.prodpilot`, which ProdPilot
  refuses to write a credential-shaped key into.
- The token is placed in the push URL for a single `git push` and never written
  into the repository's configuration, and returned output is scrubbed of it.
- Repository secrets for the CI/CD workflow are encrypted on your machine with
  the repository's public key before they are sent to GitHub.
- Secrets are given to the test container when it runs, never as build
  arguments, which would record them in the image history.
- `.env.production` is only written when Git ignores it.
- No report format prints a credential. `prodpilot report`, the dry-run preview
  and `prodpilot audit` show a found secret only as its file, its line, its first
  four characters and its length.

## What leaves your machine

ProdPilot runs locally and calls no AI service. It talks to GitHub (pushes of
the files it generated, repository secrets, workflow status), to Render
(creating the service with your sealed environment values, deploy status and
logs), and, while building your project, to Docker Hub and the npm registry.
It sends no telemetry.

The GitHub Action, when you use it, uploads its SARIF report to your
repository's GitHub code scanning. The report holds rule identifiers, file paths,
line numbers and messages, with any secret masked as above. Set `sarif: "false"`
to keep it out of code scanning.

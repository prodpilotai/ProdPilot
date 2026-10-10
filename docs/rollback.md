# Redeploys and rollback

## Redeploying an existing service

ProdPilot records the Render service it creates in the project's
`.env.prodpilot`. When that file names a service that still exists, the next
deploy, from `prodpilot_deploy` or `prodpilot deploy`, deploys the pushed commit
to that service instead of creating a second one. The service's environment
values on Render are left as they are. A project with no stored service, or
whose stored service was deleted, gets a new service created with the sealed
values, as in 1.0.0.

## Rollback after a failed smoke test

When the smoke test fails on a redeploy, ProdPilot:

1. finds the last deploy that was live before the failed one and built a
   different commit, from Render's own deploy list, never guessed;
2. rolls the service back to it;
3. monitors the rollback deploy the way any deploy is monitored, for up to ten
   minutes;
4. checks the health path again;
5. reports the original failure and the result of the rollback together.

CI/CD wiring does not run after a failed smoke test, rolled back or not.

| Situation | What happens |
| --- | --- |
| The service's first deploy | Nothing to roll back to; the report says so. |
| No earlier deploy of a different commit was live | Nothing is rolled back; the report says so. |
| The rollback succeeds | The report names the deploy restored and says the health path answers. |
| The rollback fails or does not go live | The run stops with both failures and the dashboard address. It is never retried. |
| The rollback is live but the health path still fails | Both are reported, with the dashboard address. |

A deploy can be rolled back to only while Render still holds its build: Render
keeps builds for a limited number of recent deploys, depending on the workspace
plan ([render.com/docs/rollbacks](https://render.com/docs/rollbacks)). In the
live test recorded in [releases/1.1.0-verification.md](releases/1.1.0-verification.md),
a rollback to a month-old deploy was refused by Render, and a rollback to a
deploy from earlier the same day restored the service in 16.6 seconds.

Rolling back through Render's API does not turn off the service's autodeploy,
so pushing the failed commit again deploys it again. Fix or revert it first.

## Turning rollback off

Rollback is on by default. To keep 1.0.0's behaviour, leaving a deploy that
failed its smoke test in place:

- `prodpilot deploy --no-rollback`
- the deploy tool's `rollback` input set to `false`

## For provider authors

Rollback is an optional capability. The four-method provider interface, `deploy`,
`poll_status`, `get_logs` and `set_env`, is unchanged. A provider that also
offers `find` and `redeploy` (the `Redeploy` protocol) and `previous_live` and
`rollback` (the `Rollback` protocol) gets redeploys and rollback; one that does
not keeps working exactly as before.

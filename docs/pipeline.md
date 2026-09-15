# Full pipeline integration record

Module 7.3 from Section 8 of the Phased Implementation Plan: an end-to-end run
across all phases on multiple sample repositories, including deliberately broken
ones. Every sample was taken through the whole chain, the audit, the fix loop,
the scoring gate and the nine ProdPush stages, and every stage's real outcome is
recorded below.

## How the run was made

`tests/pipeline.py` runs the chain on one sample at a time and records a trace;
`python tests/pipeline.py OUT.json` runs every sample, and
`python tests/pipeline.py --markdown OUT.json` produces the tables here.

Real in every run: the audit; every fix contract and the edits it causes; the
verifier that decides each fix; the regression guard; the loop and its budget of
3 attempts per issue and 5 cycles; the gate with the promoted model and a real
build of the project in Docker; and all nine ProdPush stages with their own code.
Each sample is a real Git repository, as a developer's project is. Stage 3 builds
the real image and runs the real container. Stage 4 makes a real commit and a
real push, into a bare repository on disk.

Substituted, exactly as `tests/test_prodpush.py` substitutes them, because no
run may touch a live service: the Render API, the GitHub API, and the deployed
application stage 7 probes. The origin remote names GitHub and pushes to it are
rewritten to the local mirror. So stages 5 to 8 run their own code against
scripted answers; they prove the chain hands each stage what it needs, not that
Render accepted a service.

The agent. `tests/apply.py` stands in for the IDE agent. It applies a content
contract literally, which is all an agent can correctly do with one. It cannot
author a constraint contract, which deliberately carries no content, so every
DYNAMIC-DELEGATED rule comes back unapplied and goes to manual review. That is a
limit of this run, stated again under the metrics, not a result about delegated
fixes.

Nothing is hidden. An exception anywhere in the chain is recorded with its
traceback as an unhandled failure.

The samples are the 22 committed under `tests/samples` and one derived variant.
No committed sample is a clean React project that can build: `react_vite_ready`
has no `index.html`, and its committed Dockerfile runs `npm ci` with no lockfile.
The variant `react_vite_ready+entry` adds the standard Vite entry page and a
`package-lock.json` written by npm itself, the two things any real React project
has, and nothing else. One committed fixture was changed, with approval:
`react_vite_ready`'s Dockerfile carried the same crashing `USER nginx` as
ProdPilot's own React template, defect 6 below, and gained the same one line the
template did. Its audit results are unchanged.

## Result

23 samples were run through the whole chain on the fixed code. 8 cleared the scoring gate and 6 completed all nine ProdPush stages, 4 of them projects that started below the gate's threshold and were brought through it by the fix loop. 15 were refused at the gate, each with its reason below. 2 cleared the gate and then stopped at a later stage: `node_express_hardened` at docker build test; `node_express_secure` at docker build test. No run recorded an unhandled failure.

## The run, sample by sample

| Sample | Stack | Start | Cycles | Fixed S/P/D | Review | Gate score | Gate | ProdPush |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `amb_two_drivers` | node_express | 23 | 1 | 12/5/0 | 4 | 89 | refused | stopped at scoring gate |
| `amb_two_entries` | node_express | 12 | 2 | 7/2/0 | 12 | 53 | refused | stopped at scoring gate |
| `amb_two_lockfiles` | node_express | 16 | 2 | 14/1/0 | 6 | 77 | refused | stopped at scoring gate |
| `amb_two_roots` | node_express | 23 | 2 | 12/4/0 | 4 | 90 | refused | stopped at scoring gate |
| `amb_unnamed_secret` | node_express | 12 | 1 | 12/5/0 | 5 | 83 | refused | stopped at scoring gate |
| `ambiguous_fullstack` | unrecognized | 0 | 1 | 0/0/0 | 0 | None | refused | stopped at scoring gate |
| `docker_ok` | node_express | 56 | 1 | 3/3/0 | 1 | 100 | ready | wired |
| `node_express_api` | node_express | 14 | 2 | 14/5/0 | 2 | 100 | ready | wired |
| `node_express_gated` | node_express | 100 | 1 | 0/0/0 | 0 | 100 | ready | wired |
| `node_express_hardened` | node_express | 69 | 1 | 3/3/0 | 2 | 100 | ready | stopped at docker build test |
| `node_express_insecure` | node_express | 13 | 2 | 13/5/0 | 5 | 96 | ready | wired |
| `node_express_ready` | node_express | 67 | 1 | 6/1/0 | 2 | 90 | refused | stopped at scoring gate |
| `node_express_secrets` | node_express | 0 | 2 | 4/2/0 | 7 | 50 | refused | stopped at scoring gate |
| `node_express_secure` | node_express | 43 | 1 | 9/4/0 | 2 | 100 | ready | stopped at docker build test |
| `node_express_wildcard` | node_express | 9 | 2 | 14/5/0 | 3 | 96 | ready | wired |
| `react_vite_app` | react_vite | 29 | 1 | 8/3/0 | 4 | 99 | refused | stopped at scoring gate |
| `react_vite_hardened` | react_vite | 43 | 1 | 8/3/0 | 3 | 100 | refused | stopped at scoring gate |
| `react_vite_noroute` | react_vite | 29 | 1 | 8/3/0 | 4 | 99 | refused | stopped at scoring gate |
| `react_vite_ready` | react_vite | 99 | 1 | 0/0/0 | 1 | 99 | refused | stopped at scoring gate |
| `react_vite_ready+entry` | react_vite | 98 | 1 | 0/0/0 | 2 | 98 | ready | wired |
| `react_vite_ts_config` | react_vite | 29 | 1 | 8/3/0 | 3 | 100 | refused | stopped at scoring gate |
| `unrecognized_python_service` | unrecognized | 0 | 1 | 0/0/0 | 0 | None | refused | stopped at scoring gate |
| `vite_without_react` | unrecognized | 0 | 1 | 0/0/0 | 0 | None | refused | stopped at scoring gate |

| Sample | Gate | Pre-flight | Sealing | Build | Push | Deploy | Monitor | Smoke | CI/CD |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `amb_two_drivers` | FAIL | not reached | not reached | not reached | not reached | not reached | not reached | not reached | not reached |
| `amb_two_entries` | FAIL | not reached | not reached | not reached | not reached | not reached | not reached | not reached | not reached |
| `amb_two_lockfiles` | FAIL | not reached | not reached | not reached | not reached | not reached | not reached | not reached | not reached |
| `amb_two_roots` | FAIL | not reached | not reached | not reached | not reached | not reached | not reached | not reached | not reached |
| `amb_unnamed_secret` | FAIL | not reached | not reached | not reached | not reached | not reached | not reached | not reached | not reached |
| `ambiguous_fullstack` | FAIL | not reached | not reached | not reached | not reached | not reached | not reached | not reached | not reached |
| `docker_ok` | pass | pass | n/a | pass | pass | pass | pass | pass | pass |
| `node_express_api` | pass | pass | n/a | pass | pass | pass | pass | pass | pass |
| `node_express_gated` | pass | pass | n/a | pass | pass | pass | pass | pass | pass |
| `node_express_hardened` | pass | pass | n/a | FAIL | not reached | not reached | not reached | not reached | not reached |
| `node_express_insecure` | pass | pass | n/a | pass | pass | pass | pass | pass | pass |
| `node_express_ready` | FAIL | not reached | not reached | not reached | not reached | not reached | not reached | not reached | not reached |
| `node_express_secrets` | FAIL | not reached | not reached | not reached | not reached | not reached | not reached | not reached | not reached |
| `node_express_secure` | pass | pass | n/a | FAIL | not reached | not reached | not reached | not reached | not reached |
| `node_express_wildcard` | pass | pass | n/a | pass | pass | pass | pass | pass | pass |
| `react_vite_app` | FAIL | not reached | not reached | not reached | not reached | not reached | not reached | not reached | not reached |
| `react_vite_hardened` | FAIL | not reached | not reached | not reached | not reached | not reached | not reached | not reached | not reached |
| `react_vite_noroute` | FAIL | not reached | not reached | not reached | not reached | not reached | not reached | not reached | not reached |
| `react_vite_ready` | FAIL | not reached | not reached | not reached | not reached | not reached | not reached | not reached | not reached |
| `react_vite_ready+entry` | pass | pass | n/a | pass | pass | pass | pass | pass | pass |
| `react_vite_ts_config` | FAIL | not reached | not reached | not reached | not reached | not reached | not reached | not reached | not reached |
| `unrecognized_python_service` | FAIL | not reached | not reached | not reached | not reached | not reached | not reached | not reached | not reached |
| `vite_without_react` | FAIL | not reached | not reached | not reached | not reached | not reached | not reached | not reached | not reached |

## The handshake refusal carried from module 7.1

In module 7.1 the Copilot CLI's first connection to ProdPilot in VS Code was
refused with `-32022`. Six attempts that morning failed; at 11:40 the client
retried and connected. `docs/compatibility.md` left the fix to this module.

What happens. The MCP SDK ProdPilot is built on, `mcp` 2.0.0, serves two
protocol eras and fixes a connection's era from the client's first request. The
Copilot CLI first sends `server/discover` at the 2026-07-28 version. ProdPilot
takes about a second to answer its first request, 1.17, 1.11, 1.11 and 1.08
seconds over four measured starts, and 0.94 of the 1.03 seconds its server module
takes to import is the SDK's own import. A client whose probe times out sooner,
and which then sends the older `initialize` handshake on the same connection,
has already made that connection a 2026-07-28 one, and the SDK refuses the
handshake.

Reproduced. `tests/test_handshake.py` sends the probe and the handshake back to
back, the order the server sees them in when a client's probe times out during
startup. The handshake is refused every time with `-32022`, and the refusal
carries the versions to use: `{"supported": ["2026-07-28"], "requested":
"2025-11-25"}`.

Why refusing is correct. The SDK does it by design: its serving loop documents
that "a later claim from the other era is refused". The refusal is the
protocol's negotiation signal rather than a dead end, and the SDK's own client
handles it. Its connect logic, `mcp/client/_probe.py`, says so in as many words:
"The fallback handshake itself can be answered with -32022, e.g. a probe that
timed out client-side but succeeded on a slow-starting server locked the
connection modern before the pipelined initialize arrived. That code is itself
positive modern evidence (it names the server's versions), so it triggers one
re-probe at a mutual version instead of failing the connect."
`tests/test_handshake.py` proves that path against the real server: probing
again on the same connection connects, lists all five tools and answers a call.
A client using either era alone connects on the first attempt.

Why no server change. Only two server-side changes would avoid the refusal:
serving one era only, which the SDK exposes only through a private function, or
accepting a handshake on a connection that already opened in the other era,
which the protocol forbids. Starting faster cannot close the gap, since almost
all of the startup is the SDK itself. The failure is in the Copilot CLI's client,
which does not re-probe on the same connection. Where a developer meets it, the
client's own reconnect, or restarting the server from MCP: List Servers,
connects, as it did at 11:40.

Status: resolved as intentional, correct server behaviour, with the evidence
pinned in the suite. It is a known, handled failure path, not an open one.

## Defects the run found, and how each was closed

The run was first made on the code as it stood. On that run no sample completed
the chain: the loop and the gate took five broken Express projects to between
96 and 100, and every one of them, with the clean demo and the React variant,
then failed stage 3's real Docker build. Six defects in closed phases were
found behind that and behind the results that followed it. Each was flagged
before it was changed, each fix was approved, and each has tests.

1. Stage 3 did not run the container the way Render runs it (Phase 6,
   `buildtest.py`). It gave the container no `PORT`, and it could only reach a
   port the image declared with `EXPOSE`. Render needs neither: "The default
   value of PORT is 10000 for all Render web services", and "If you bind your
   HTTP server to a different port, Render is usually able to detect and use
   it." So stage 3 refused the demo Phase 6 had deployed live, whose code falls
   back to port 3000 when `PORT` is unset; on the same copy it was not healthy
   with no environment, healthy with a 200 given `PORT=10000`, and not healthy
   given `PORT=3000`. And no Dockerfile the fix loop writes declares a port, so
   none of them could ever pass. A first fix, giving every container
   `PORT=10000`, was replaced when the run showed it moved `docker_ok`, which
   exposes 3000 and reads `PORT`, away from the port being probed. Fix: the
   container is given the project's own sealed `PORT`, else the port its image
   exposes, else Render's 10000, and that port is published when the image
   exposes none. The run then found two more faults in the same stage. It
   probed only the first published port, and nginx's base image exposes 80
   beneath the 8080 a Dockerfile adds, so it probed 80 and refused a healthy
   container; and it reported a container that crashed on start as one that
   "publishes no port". Stage 3 now asks every published port until one
   answers, and reports a container that stopped as having exited on start,
   with its exit code and the line of its output that says why, which travels
   into the stage's own record.
2. `npm ci` with no lockfile (Phase 3, `extraction.py`). The generated
   Dockerfile, both multi-stage variants and the CI workflow all installed with
   `npm ci`, which refuses to run without a `package-lock.json`. In a container on
   the same project `npm ci` failed and `npm install` succeeded. Fix: `npm ci`
   when a lockfile exists, `npm install` when there is none, at all five sites.
3. ENV-002 did not follow a variable (Phase 2, `astchecks.py`). The critical
   rule failed `const port = process.env.PORT || 3000; app.listen(port)`, the
   usual way to write it, and on a plain `http` server its contract would have
   told an agent to write `app.listen` into a file with no `app`. Fix: a port
   passed as a variable passes when the variable's declaration reads the
   environment; a literal still fails.
4. OBS-006 failed ProdPilot's own nginx file (Phase 2, `filechecks.py`). The
   check failed on the text `access_log off` anywhere, and ProdPilot's own
   template turns access logging off only for `/health`, so every React project
   ProdPilot repaired failed the rule and its content contract could never fix
   it. Fix: logging turned off inside a `location` block is not logging turned
   off for the server; turned off for the whole server still fails.
5. Stage 8 let an unusable key crash the pipeline (Phase 6, `cicd.py`). When
   sealing secrets, a repository public key PyNaCl could not read raised
   `ValueError` out of `prodpush.run`, which is meant never to raise. The run
   met it through the harness's own scripted key, which did not decode to 32
   bytes and has been replaced with a real one, but the path is real. Fix: an
   unusable key stops stage 8 with "the repository public key is not a valid
   key", like every other stage 8 failure.
6. ProdPilot's React Dockerfile could not start (Phase 3, `extraction.py` and
   `templates.py`). Both React templates, and SEC-005's fix, switch nginx to
   `USER nginx`, and nginx's image leaves its cache and pid file to root, so
   every React container ProdPilot repaired stopped at once with `mkdir()
   "/var/cache/nginx/client_temp" failed (13: Permission denied)`. Render never
   runs this Dockerfile, since it publishes a React project as a static site, so
   no deployment was affected, but no React project could pass stage 3. Proven
   in Docker before the change: with one line giving those paths to the nginx
   user, the container ran and `/health` answered 200. Fix: that line, in both
   templates and in SEC-005's content, before `USER nginx`.

## Failure paths recorded, not changed

Each of these is handled: the chain stops or routes the rule to manual review
with an exact reason, and nothing crashes. None is a small correction, or none
needs one.

- A CORS fix that breaks the environment template. SEC-003's contract makes the
  code read `CORS_ORIGIN`; where ENV-001 had already written `.env.example`, the
  new key makes ENV-001 fail, the regression guard reverts SEC-003, and the rule
  goes to manual review saying so. SEC-003 is critical, so the gate stays shut
  until a person adds the key. A fix means contracts declaring the environment
  keys they introduce, as they already declare packages, which is a change to the
  contract format rather than a small correction.
- Critical content rules that edit a file a later rule creates. SCR-001, SEC-001
  and on React SCR-003, SEC-005 and SEC-006 insert into `.gitignore`, the
  Dockerfile or `nginx.conf` before the lower priority rule that creates the file
  has run. They fail their first cycle with "no file at", and pass by the end of
  the run wherever the rule that creates the file ran. On three projects that
  rule was itself refused for ambiguity, `amb_two_entries` and
  `amb_two_lockfiles` over entry points and lockfiles and `node_express_secrets`
  with no entry point at all, so no Dockerfile ever exists and SEC-001 stays
  failing, in manual review beside the refusal that explains it. This is the
  precondition behaviour the Phase 3 report recorded; the gate's second cycle is
  what corrects it where it can, and it lowers the first attempt rate under the
  metrics. The Phase 3 report's statement that an
  insert at the end of a missing file creates it does not hold for SCR-001's
  contract, which carries no such instruction.
- A project with three entry points. In `amb_two_entries` three files each build
  an Express app. Each content contract named `src/app.js`, the file its finding
  named first, and was applied there; the rule then passed for that file and
  still failed on `src/index.js`, which the retries never named. The rule stays
  in manual review. Contracts that address several files at once would be a
  change to the contract, not a small correction, and the sample exists to be
  ambiguous.
- An error handler that has to move. API-003's content was inserted after the
  routes of `node_express_insecure` and the rule still failed, the limit the
  Phase 3 report recorded: an existing error handler has to be moved rather than
  added to, which an insert cannot do.
- A budget that counts attempts, not progress. `node_express_secrets` holds four
  credential strings in one file. Each SCR-002 attempt moved one into the
  environment, but the rule passes only when all are gone, so after three
  attempts one remained and the rule went to review. Changing what the budget
  counts is a change to the loop, not a small correction.
- Refusals by design. Where a project is ambiguous, extraction names the
  candidates and refuses rather than guessing: three entry points, two lockfiles,
  two database drivers, routes under two roots, a secret bound to no name.
- Projects outside the two supported stacks are refused at the gate before any
  stage runs.
- Two fixtures that were never runnable applications. `node_express_hardened`
  and `node_express_secure` cleared the gate at 100 and then stopped at stage 3,
  and the cause is in the committed samples, not in anything the loop did: each
  requires a module the sample does not contain, `../controllers/orderController`
  and `../models/User`, so the container exits on start with `Cannot find
  module`. Both were written to exercise audit rules and were never complete
  applications. The audit has no rule for a `require` of a file that does not
  exist, which is why the gate passed them; stage 3's real build and run is what
  caught them, which is the stage doing its job. Before this module, stage 3
  reported such a failure only as "container not healthy"; with defect 1's fix
  its record now says the container exited on start, with the exit code and the
  application's own error, the missing module, so a developer sees why without
  running the container.

## Each sample

### `amb_two_drivers`

Gate: score 89 but 1 critical rule(s) still fail: SEC-003

Manual review:

- SCR-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-003 (STATIC): fixing SEC-003 broke ENV-001, which passed before it, so the change was reverted
- CON-002 (DYNAMIC-PARAMETRIC): 2 database drivers are declared, so the pool to configure is unclear. Candidates: mysql, postgres

### `amb_two_entries`

Gate: score 53 but 3 critical rule(s) still fail: SEC-001, SEC-003, SEC-004

Manual review:

- SCR-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change
- SEC-003 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change
- SEC-004 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change
- BLD-001 (DYNAMIC-PARAMETRIC): 3 candidate entry points exist and package.json names none. Candidates: src/server.js, src/app.js, src/index.js
- BLD-004 (DYNAMIC-PARAMETRIC): 3 candidate entry points exist and package.json names none. Candidates: src/server.js, src/app.js, src/index.js
- BLD-006 (DYNAMIC-PARAMETRIC): the Dockerfile declares no base image to build from
- API-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change
- OBS-002 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change
- OBS-003 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change
- OBS-004 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change
- API-002 (DYNAMIC-PARAMETRIC): the application mounts no paths to version

### `amb_two_lockfiles`

Gate: score 77 but 1 critical rule(s) still fail: SEC-001

Manual review:

- SCR-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change
- BLD-001 (DYNAMIC-PARAMETRIC): 2 lockfiles are present, so the package manager is unclear. Candidates: package-lock.json, yarn.lock
- BLD-003 (DYNAMIC-PARAMETRIC): 2 lockfiles are present, so the package manager is unclear. Candidates: package-lock.json, yarn.lock
- BLD-006 (DYNAMIC-PARAMETRIC): the Dockerfile declares no base image to build from
- API-002 (DYNAMIC-PARAMETRIC): the application mounts no paths to version

### `amb_two_roots`

Gate: score 90 but 1 critical rule(s) still fail: SEC-003

Manual review:

- SCR-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-003 (STATIC): fixing SEC-003 broke ENV-001, which passed before it, so the change was reverted
- API-002 (DYNAMIC-PARAMETRIC): routes are mounted under 2 different roots, so one versioned prefix cannot be derived. Candidates: /billing, /orders

### `amb_unnamed_secret`

Gate: score 83 but 2 critical rule(s) still fail: SCR-002, SEC-003

Manual review:

- SCR-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SCR-002 (DYNAMIC-PARAMETRIC): the value is not bound to a named identifier, so no environment variable name can be derived from it. Candidates: PORT
- SEC-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-003 (STATIC): fixing SEC-003 broke ENV-001, which passed before it, so the change was reverted
- GIT-003 (DYNAMIC-DELEGATED): unresolved after 3 attempt(s): the rule still fails after the agent's change

### `ambiguous_fullstack`

Gate: ambiguous project, express and react with vite are both declared. Split the frontend and backend into separate project roots so each can be matched to its own blueprint

### `docker_ok`

Gate: score 100 meets the threshold of 90 with no critical failures, and the model estimates a 67% chance of deploying, at or above its operating point of 37%

Wired: 3 commit(s) pushed to the mirror, 3 Render call(s), 3 secret(s) sealed for GitHub.

Manual review:

- SCR-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.

### `node_express_api`

Gate: score 100 meets the threshold of 90 with no critical failures, and the model estimates a 84% chance of deploying, at or above its operating point of 37%

Wired: 3 commit(s) pushed to the mirror, 3 Render call(s), 3 secret(s) sealed for GitHub.

Manual review:

- SCR-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.

### `node_express_gated`

Gate: score 100 meets the threshold of 90 with no critical failures, and the model estimates a 84% chance of deploying, at or above its operating point of 37%

Wired: 2 commit(s) pushed to the mirror, 3 Render call(s), 3 secret(s) sealed for GitHub.

### `node_express_hardened`

Gate: score 100 meets the threshold of 90 with no critical failures, and the model estimates a 74% chance of deploying, at or above its operating point of 37%

Stopped at docker build test: node_express_hardened: image built, container not healthy: the container exited on start with code 1: Error: Cannot find module '../controllers/orderController'

Manual review:

- SCR-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.

### `node_express_insecure`

Gate: score 96 meets the threshold of 90 with no critical failures, and the model estimates a 82% chance of deploying, at or above its operating point of 37%

Wired: 3 commit(s) pushed to the mirror, 3 Render call(s), 3 secret(s) sealed for GitHub.

Manual review:

- SCR-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- API-003 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change
- STR-001 (DYNAMIC-DELEGATED): unresolved after 3 attempt(s): the rule still fails after the agent's change
- STR-002 (DYNAMIC-DELEGATED): unresolved after 3 attempt(s): the rule still fails after the agent's change

### `node_express_ready`

Gate: score 90 but 1 critical rule(s) still fail: SEC-003

Manual review:

- SEC-003 (STATIC): fixing SEC-003 broke ENV-001, which passed before it, so the change was reverted
- API-002 (DYNAMIC-PARAMETRIC): the application mounts no paths to version

### `node_express_secrets`

Gate: score 50 but 2 critical rule(s) still fail: SCR-002, SEC-001

Manual review:

- SCR-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SCR-002 (DYNAMIC-PARAMETRIC): unresolved after 3 attempt(s): the rule still fails after the change
- SEC-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change
- BLD-001 (DYNAMIC-PARAMETRIC): no entry point is declared and none of the conventional names exist
- BLD-004 (DYNAMIC-PARAMETRIC): no entry point is declared and none of the conventional names exist
- BLD-006 (DYNAMIC-PARAMETRIC): the Dockerfile declares no base image to build from
- GIT-003 (DYNAMIC-DELEGATED): unresolved after 3 attempt(s): the rule still fails after the agent's change

### `node_express_secure`

Gate: score 100 meets the threshold of 90 with no critical failures, and the model estimates a 82% chance of deploying, at or above its operating point of 37%

Stopped at docker build test: node_express_secure: image built, container not healthy: the container exited on start with code 1: Error: Cannot find module '../models/User'

Manual review:

- SCR-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.

### `node_express_wildcard`

Gate: score 96 meets the threshold of 90 with no critical failures, and the model estimates a 84% chance of deploying, at or above its operating point of 37%

Wired: 3 commit(s) pushed to the mirror, 3 Render call(s), 3 secret(s) sealed for GitHub.

Manual review:

- SCR-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-001 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- BLD-004 (DYNAMIC-PARAMETRIC): unresolved after 3 attempt(s): the rule still fails after the change

### `react_vite_app`

Gate: score 99 meets the threshold, but the model estimates a 5% chance of deploying, below its operating point of 37%

Manual review:

- SCR-003 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-005 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-006 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- STR-003 (DYNAMIC-DELEGATED): unresolved after 3 attempt(s): the rule still fails after the agent's change

### `react_vite_hardened`

Gate: score 100 meets the threshold, but the model estimates a 6% chance of deploying, below its operating point of 37%

Manual review:

- SCR-003 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-005 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-006 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.

### `react_vite_noroute`

Gate: score 99 meets the threshold, but the model estimates a 5% chance of deploying, below its operating point of 37%

Manual review:

- SCR-003 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-005 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-006 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- STR-004 (DYNAMIC-DELEGATED): unresolved after 3 attempt(s): the rule still fails after the agent's change

### `react_vite_ready`

Gate: score 99 meets the threshold, but the model estimates a 5% chance of deploying, below its operating point of 37%

Manual review:

- STR-003 (DYNAMIC-DELEGATED): unresolved after 3 attempt(s): the rule still fails after the agent's change

### `react_vite_ready+entry`

Gate: score 98 meets the threshold of 90 with no critical failures, and the model estimates a 83% chance of deploying, at or above its operating point of 37%

Wired: 2 commit(s) pushed to the mirror, 3 Render call(s), 3 secret(s) sealed for GitHub.

Manual review:

- STR-003 (DYNAMIC-DELEGATED): unresolved after 3 attempt(s): the rule still fails after the agent's change
- GIT-007 (DYNAMIC-DELEGATED): unresolved after 3 attempt(s): the rule still fails after the agent's change

### `react_vite_ts_config`

Gate: score 100 meets the threshold, but the model estimates a 6% chance of deploying, below its operating point of 37%

Manual review:

- SCR-003 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-005 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.
- SEC-006 (STATIC): unresolved after 3 attempt(s): the rule still fails after the change. It passes by the end of the run.

### `unrecognized_python_service`

Gate: no package.json found at the project root

### `vite_without_react`

Gate: vite is present but react is not. Only React with Vite is supported in v1

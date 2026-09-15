# Metrics

Module 7.4 from Section 8 of the Phased Implementation Plan: the determinism
ratio, the DYNAMIC-DELEGATED success rate, and fix reliability by type, "all
tracked and reported as stated in the success metrics", which are Section 13 of
the Complete Solution Document. Every number here is measured, from the code or
from module 7.3's full-chain run recorded in [pipeline.md](pipeline.md), and
each says where it comes from.

## Determinism ratio

Section 13, item 16: "The proportion of the 40+ rule ruleset classified STATIC
or DYNAMIC-PARAMETRIC versus DYNAMIC-DELEGATED is measured and reported, as
direct evidence of how much of production readiness enforcement is fully
deterministic under this design."

| Fix type | Rules | Share of 50 |
| --- | --- | --- |
| STATIC | 28 | 56.0% |
| DYNAMIC-PARAMETRIC | 16 | 32.0% |
| DYNAMIC-DELEGATED | 6 | 12.0% |
| Deterministic, STATIC or DYNAMIC-PARAMETRIC | 44 | 88.0% |

The six DYNAMIC-DELEGATED rules are GIT-003, GIT-007, STR-001, STR-002, STR-003
and STR-004.

It is computed by `determinism_ratio()` in `src/prodpilot/rules.py`, from each
rule's own fix type in the frozen ruleset. `tests/test_rules.py`,
`test_determinism_ratio_is_measured_not_asserted`, checks that the function
agrees with a fresh count of those fix types; by design it does not fix the
value, so a change to the ruleset changes the number rather than breaking a
test. Called on the code as committed, it returns 0.88. The number module 2.1
first reported, 88.0 percent, 44 of 50, still holds exactly.

## How the run measures a fix

Every contract the loop sent in module 7.3's run was recorded with what the
executor did and what the verifier then found. From those records, per fix type:

- An instance is one rule on one sample that the loop sent at least one contract
  for.
- First attempt verified: the first contract sent for it passed the rule's own
  checker.
- Verified within budget: one of its first three contracts passed, the loop's
  retry budget.
- Passing at the end of the run: the rule passes in an audit taken as the loop
  left the project, whether or not it was attempted again. A rule whose file a
  later fix created passes here without a second attempt.
- Applied as written: of the contracts the executor actually carried out, how
  many the verifier confirmed. This is the direct test of "deterministic by
  construction".
- A fix the verifier confirmed and the regression guard then reverted, because
  it broke a rule that had passed, is counted as reverted, never as a success.

## Fix reliability by type

Section 13, item 12: "STATIC fixes: 100 percent deterministic by construction.
DYNAMIC-PARAMETRIC fixes: 100 percent deterministic once extraction succeeds,
with an ambiguity rate tracked separately. DYNAMIC-DELEGATED fixes: first-pass
verification rate tracked and reported honestly."

Measured over module 7.3's final run, 23 samples, recorded in
[pipeline.md](pipeline.md), and reproduced with
`python tests/pipeline.py --metrics RUN.json` from that run's traces.

| Fix type | Instances | Contracts sent | First attempt verified | Verified within budget | Passing at the end of the run | Applied as written, verified |
| --- | --- | --- | --- | --- | --- | --- |
| STATIC | 201 | 285 | 155 of 201 (77.1%) | 155 of 201 (77.1%) | 187 of 201 (93.0%) | 155 of 177 (87.6%) |
| DYNAMIC-PARAMETRIC | 59 | 63 | 57 of 59 (96.6%) | 57 of 59 (96.6%) | 57 of 59 (96.6%) | 57 of 60 (95.0%) |
| DYNAMIC-DELEGATED | 9 | 27 | 0 of 9 (0.0%) | 0 of 9 (0.0%) | 0 of 9 (0.0%) | none |

| Fix type | Not verified within budget, because | Instances |
| --- | --- | --- |
| STATIC | the file it edits was not created yet | 35 |
| STATIC | applied, and the rule still failed | 6 |
| STATIC | verified, then reverted by the regression guard | 4 |
| STATIC | the executor could not place the anchor | 1 |
| DYNAMIC-PARAMETRIC | applied, and the rule still failed | 1 |
| DYNAMIC-PARAMETRIC | the executor could not place the anchor | 1 |
| DYNAMIC-DELEGATED | no author for a constraint contract | 9 |

DYNAMIC-PARAMETRIC ambiguity rate: 15 of 74 (20.3%) rule instances were refused by extraction before any contract was sent.

| Refused because | Instances |
| --- | --- |
| the Dockerfile declares no base image to build from | 3 |
| the application mounts no paths to version | 3 |
| 3 candidate entry points exist and package.json names none | 2 |
| 2 lockfiles are present, so the package manager is unclear | 2 |
| no entry point is declared and none of the conventional names exist | 2 |
| 2 database drivers are declared, so the pool to configure is unclear | 1 |
| routes are mounted under 2 different roots, so one versioned prefix cannot be derived | 1 |
| the value is not bound to a named identifier, so no environment variable name can be derived from it | 1 |


### STATIC against "100 percent deterministic by construction"

A STATIC contract is deterministic: the same project always receives the same
change. It is not 100 percent reliable. Of the 177 contracts the executor
applied as written, 155 were verified, 87.6 percent; 77.1 percent of rule
instances passed on the first contract, and 93.0 percent pass by the end of the
run. Every one of the 22 applied contracts that did not verify is accounted
for, and none of them produced the wrong content for the file it named:

- 15 are in `amb_two_entries`, where three files each build an Express app. Each
  contract made the file it named pass, and the rule still failed on another.
- 4 are SEC-003, which the verifier passed and the regression guard then
  reverted, because the new CORS key broke ENV-001 on four projects.
- 3 are API-003 on `node_express_insecure`, where an existing error handler has
  to be moved, which an insert cannot do.

The 35 instances whose file did not exist yet are critical rules that edit a
file a lower priority rule creates. They pass by the end of the run wherever
that rule ran; on three projects it was itself refused for ambiguity, so the
file never existed.

### DYNAMIC-PARAMETRIC against "100 percent deterministic once extraction succeeds"

The ambiguity rate is tracked as the document asks: 15 of 74 rule instances,
20.3 percent, were refused by extraction before any contract was sent, each
with the candidates it would not choose between. Where extraction succeeded, 57
of the 60 contracts applied as written were verified, 95.0 percent, and 57 of 59
instances passed on the first contract. The rest are not a wrong extraction:

- SCR-002 on `node_express_secrets`: four credentials in one file. Each of the
  three contracts moved one into the environment, and the rule passes only when
  all four are gone, so the budget ran out with one left.
- BLD-004 on `node_express_wildcard`: `tests/apply.py`, the stand-in for the
  agent, has no locator for the `package:scripts` anchor the contract names, so
  it never applied it. A real agent can find a `scripts` block; this is a limit
  of the executor, not of the contract, and it is still counted as not
  verified.

So the figure is 95.0 percent, not 100, and the gap is a budget that counts
attempts rather than progress and one anchor the test executor cannot place.

### DYNAMIC-DELEGATED success rate

Nine instances of all six delegated rules reached the loop in the final run:
GIT-003 twice, STR-003 three times, and STR-001, STR-002, STR-004 and GIT-007
once each. The loop sent 27 constraint contracts for them, and none was
verified: first attempt 0 of 9, within budget 0 of 9, passing at the end of
the run 0 of 9. Each came back unapplied because the executor cannot author a
change, so these numbers measure the executor, as the next section states. The
first-pass verification rate Section 13 asks for, a live agent's change passing
the checker, has not been measured by any run in this project.

## The limit on the delegated number

The rate for DYNAMIC-DELEGATED fixes in this run measures the executor, not an
agent. `tests/apply.py` stands in for the IDE agent, and "It cannot author a
constraint contract, because that form deliberately carries no content. A
delegated rule therefore comes back unapplied, which is honest rather than
convenient: the loop then retries it, exhausts the budget, and records it for
manual review."

As the Phase 3 report stated it: "The delegated fix type is proven for
everything except an LLM authoring the change, because no development
environment agent can run inside an automated test; the contract is produced,
delivered over the real transport and independently verified, and what remains
unproven is only that a live agent given the boundary writes a change that
passes."

Phase 7 narrowed that by one step and no further. Modules 7.1 and 7.2 sent the
DYNAMIC-DELEGATED contract for STR-003 to the agents of VS Code with Copilot,
Cursor and Windsurf, and each received it intact, with its requirement, boundary
and forbidden list, checked against each client's own record of the call. The
agents were told not to apply it. So the first-pass verification rate Section 13
asks for, a live agent's change passing the checker, has not been measured by
any run in this project.

## After Phase 7

Content contracts now declare the environment keys their content reads, so
SEC-003's fix no longer breaks ENV-001 and is no longer reverted. The whole
chain was run again on the same 23 samples, recorded in
[pipeline.md](pipeline.md). Everything above describes module 7.3's run and is
left as measured; this is the same measure on the new run.

| STATIC | Module 7.3's final run | After |
| --- | --- | --- |
| First attempt verified | 155 of 201 (77.1%) | 159 of 201 (79.1%) |
| Passing at the end of the run | 187 of 201 (93.0%) | 191 of 201 (95.0%) |
| Applied as written, verified | 155 of 177 (87.6%) | 159 of 177 (89.8%) |
| Reverted by the regression guard | 4 | 0 |

The 18 applied STATIC contracts that still do not verify are the 15 in
`amb_two_entries` and the 3 API-003 contracts on `node_express_insecure`. The
DYNAMIC-PARAMETRIC figures, the ambiguity rate of 15 of 74 and the
DYNAMIC-DELEGATED figures are unchanged.

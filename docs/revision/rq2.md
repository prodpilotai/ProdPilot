# RQ2: violations in real repositories by fix type

Produced by `python -m prodpilot.violations`. Population: the repositories
module 5.1 kept, 500 found in data/cache, 499 audited.

| Fix type | Violations | Share | Priority-weighted share |
| --- | --- | --- | --- |
| STATIC | 4881 | 65.9% | 70.3% |
| DYNAMIC-PARAMETRIC | 2045 | 27.6% | 29.1% |
| DYNAMIC-DELEGATED | 481 | 6.5% | 0.6% |
| Total | 7407 | | |

Deterministic share: 93.5% of violations, 99.4% weighted by priority.

| Stack | Violations | Deterministic share | Failing rules per repository, mean | median | range |
| --- | --- | --- | --- | --- | --- |
| node_express | 4590 | 94.9% | 17.65 | 19.0 | 7 to 23 |
| react_vite | 2817 | 91.3% | 11.79 | 12 | 6 to 16 |

| Priority | Violations | Deterministic share |
| --- | --- | --- |
| P0 | 2311 | 100.0% |
| P1 | 2733 | 100.0% |
| P2 | 557 | 100.0% |
| P3 | 83 | 0.0% |
| P4 | 1287 | 100.0% |
| P5 | 436 | 8.7% |

Repositories with at least one delegated violation: 412 of 499. Repositories failing a single rule alone: 0. Repositories failing a P0 rule: 499. Rules that fire at least once: 50 of 50.

| Most frequent failing rules | Repositories | Fix type | Priority |
| --- | --- | --- | --- |
| SCR-001 | 257 | STATIC | P0 |
| BLD-006 | 253 | DYNAMIC-PARAMETRIC | P1 |
| SEC-001 | 253 | STATIC | P0 |
| BLD-005 | 249 | STATIC | P1 |
| OBS-005 | 239 | STATIC | P4 |
| SEC-006 | 239 | STATIC | P0 |
| SEC-005 | 238 | STATIC | P0 |
| OBS-006 | 238 | STATIC | P4 |
| BLD-002 | 235 | STATIC | P1 |
| SEC-003 | 234 | STATIC | P0 |

Extraction on real repositories: 830 of 2045 parametric instances refused (40.6%), against 15 of 74 (20.3%) on the samples built to be ambiguous.

| Kind of refusal | Instances | Share of parametric instances |
| --- | --- | --- |
| ambiguity | 120 | 5.9% |
| disagreement | 115 | 5.6% |
| missing input | 149 | 7.3% |
| precondition | 446 | 21.8% |

| Refusal reason | Kind | Instances |
| --- | --- | --- |
| the Dockerfile declares no base image to build from | precondition | 445 |
| the value is not bound to a named identifier, so no environment variable name can be derived from it | missing input | 89 |
| no inline connection string was found to move | disagreement | 88 |
| routes are mounted under N different roots, so one versioned prefix cannot be derived | ambiguity | 70 |
| no supported database driver is declared in package.json | missing input | 23 |
| N different hosts appear in source, so one key cannot hold them all | ambiguity | 21 |
| no entry point is declared and none of the conventional names exist | missing input | 21 |
| N lockfiles are present, so the package manager is unclear | ambiguity | 17 |
| no credential literal was found to move | disagreement | 15 |
| package.json declares no build script, so there is nothing to build | missing input | 14 |
| mongoose pools by default, so this rule needs no fix here | disagreement | 11 |
| N database drivers are declared, so the pool to configure is unclear | ambiguity | 5 |
| N candidate entry points exist and package.json names none | ambiguity | 5 |
| the value is bound to N different names, so the codebase does not settle what to call it | ambiguity | 2 |
| package.json declares no build script, so there is nothing to serve | missing input | 2 |
| no request targets a literal host | disagreement | 1 |
| the Dockerfile declares no CMD, so the runtime stage has no command | precondition | 1 |

Not audited: lifedever__ANodeBlog (RecursionError: maximum recursion depth exceeded while decod)

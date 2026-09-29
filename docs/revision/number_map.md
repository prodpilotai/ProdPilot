# Numbers changed or added in the revision

Every number below is produced by a script in this repository. Old values are
from the manuscript as submitted on 16 September 2026.

## Corrected

| Statement | Submitted | Revised | Source |
| --- | --- | --- | --- |
| Test suite | 1,796 tests on Python 3.11 and 3.14; Fig. 3 gave 1,738 | 1,799 tests in the released suite, CI on Linux, macOS and Windows with Python 3.11 to 3.14 | facts.json, tests |
| Package version | 1.0.0rc1 | 1.0.0 | facts.json, version |
| CI/CD after the live deploy | the wait-for-deploy workflow redeployed twice | the two redeploys used the earlier workflow with a fixed 90 s wait; wait-for-deploy is implemented but not run live | facts.json, cicd |
| Largest tool response | 11,988 bytes, source unstated | 11,988 bytes for the whole MCP result: 6,431 text, 5,003 structured, plus envelope | facts.json, response |
| Node images in the build pass | "39 rows were built on Node 24" | 503 on node:lts, 128 on node:latest, 39 fell back to node:lts from majors 0 to 18 | facts.json, node |
| Huang et al. page range | 904 to 925 | 904 to 928 | Crossref, 10.1109/TSE.2025.3532759 |
| Real repositories | 499 in the text, 498 in Table VII | 500 kept, 499 audited, 498 labelled, 494 in the model's matrix | rq2.json, label.json |
| Five percent DevOps training | stated as a fact | attributed to Barbosa et al. as their report | manuscript |

## Added

| Measure | Value | Source |
| --- | --- | --- |
| Violations in real repositories | 7,407: static 4,881, parametric 2,045, delegated 481 | rq2.json |
| Deterministic share | 93.5%; 99.4% weighted by priority; Express 94.9%, React 91.3% | rq2.json |
| Failing rules per repository | Express mean 17.7, range 7 to 23; React mean 11.8, range 6 to 16 | rq2.json |
| Extraction refusals, real repositories | 830 of 2,045 (40.6%): precondition 21.8%, missing input 7.3%, ambiguity 5.9%, checker and extractor disagree 5.6% | rq2.json |
| Model D, held out | ROC AUC 0.909 (0.867 to 0.946), PR AUC 0.565 (0.464 to 0.729) | model.json |
| Model E, held out | ROC AUC 0.913 (0.869 to 0.954), PR AUC 0.710 (0.594 to 0.835) | model.json |
| Build and stack baseline | ROC AUC 0.886 (0.853 to 0.916), PR AUC 0.533 | model.json |
| Audit score baseline | ROC AUC 0.706 (0.634 to 0.776); React 0.518, Express 0.842 | model.json |
| Rule-only gate | admits 2 of 169 held-out projects, neither deployed | model.json |
| D minus E, paired bootstrap | ROC AUC -0.004 (-0.037 to 0.030); PR AUC -0.131 (-0.242 to -0.005) | model.json |
| Repeated cross validation, 10 by 5 folds | D ROC AUC 0.923 sd 0.028, PR AUC 0.685 sd 0.074; E 0.919 sd 0.025, 0.693 sd 0.077 | model.json |
| Fully fixed projects clearing 0.369 | Express non-deployers 216 of 306; React non-deployers 66 of 240 | model.json |
| Express recall at 0.3 to 0.5 | 0.200 throughout; F1-best Express cut 0.472 | model.json |
| Audit score weights | no decision changes on real repositories; at the gate, equal weights change 3 of 20, linear 1 | model.json |
| Released audit against stored features | same score on 429 of 494 real repositories | model.json |
| Synthetic negatives against their source | 163 pairs; outcome unchanged in 153; 10 deployers turned into failures, 9 by BLD-011 | label.json |
| Rules associated with deployment | 7 of 50 at 95%, against 2.5 by chance; 3 after Bonferroni | label.json |
| Built but not deployed, Express | Render reported failure 151, live without health 34, build timed out 8 | label.json |
| Run time per stage | audit 0.16 s; one repair cycle 3.2 s; whole chain 16.0 s; live deployment 82 s | timing.json |
| Delegated fixes, live agents | 10 trials, all six rules, all resolved on the first attempt | results recorded in LiveTest, verified by audit |

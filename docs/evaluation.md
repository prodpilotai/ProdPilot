# Evaluation of the build result and the monotonic constraint

What was measured before the gate policy for React was decided, and how. Every
number here was computed from the real labelled dataset, with nothing
deployed again and no network used except the npm registry for the build pass.

## The data

- 684 projects deployed to Render for real by module 5.3, 129 of which
  deployed and served their health path.
- Every one was built locally with Render's own build command by module
  builds, a week after its deployment. 9 builds could not be determined, 8
  React builds that ran past twenty minutes and 1 Express build that failed
  twice on the network. All 9 were non deployers, and they are excluded, with
  reasons, in `data/features.excluded.jsonl`.
- That leaves 675 labelled rows with 26 features each, 129 positive.
- A stratified split with seed 20260910 holds out 169 rows, 32 of them
  deployers: 95 React rows with 27 deployers and 74 Express rows with 5.

What the build pass found, by stack and outcome:

| | built | failed | undetermined |
|---|---|---|---|
| React, deployed | 108 | 1 | 0 |
| React, did not deploy | 66 | 174 | 8 |
| Express, deployed | 20 | 0 | 0 |
| Express, did not deploy | 234 | 72 | 1 |

## Five models on one split

So that the build feature and the constraint are measured separately, five
models were trained on the same training rows and scored once on the same
held out rows. Every parameter search used 5 fold cross validation on the
training rows only. Every model is calibrated with sigmoid calibration over 5
folds, and its operating threshold is the F1 best cut on out of fold
predictions over the training rows.

- A: the model in use before, GradientBoostingClassifier on 25 features
- B: the constraint only, monotonic HistGradientBoostingClassifier on 25
- C: the feature only, unconstrained HistGradientBoostingClassifier on 26
- D: both, monotonic HistGradientBoostingClassifier on 26
- E: the feature on the old estimator, GradientBoostingClassifier on 26

Held out, both stacks together. Guessing from the stack alone scores a PR AUC
of 0.269 and always answering no scores 0.189.

| | ROC AUC | PR AUC | Brier | threshold | precision | recall | F1 | tn | fp | fn | tp |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 0.772 | 0.439 | 0.127 | 0.218 | 0.364 | 0.875 | 0.514 | 88 | 49 | 4 | 28 |
| B | 0.752 | 0.379 | 0.134 | 0.256 | 0.303 | 0.719 | 0.426 | 84 | 53 | 9 | 23 |
| C | 0.912 | 0.587 | 0.087 | 0.409 | 0.614 | 0.844 | 0.711 | 120 | 17 | 5 | 27 |
| D | 0.909 | 0.565 | 0.091 | 0.369 | 0.583 | 0.875 | 0.700 | 117 | 20 | 4 | 28 |
| E | 0.913 | 0.710 | 0.084 | 0.447 | 0.614 | 0.844 | 0.711 | 120 | 17 | 5 | 27 |

Held out, each stack on its own:

| | React ROC | React PR | React Brier | React tn fp fn tp | Express ROC | Express PR | Express Brier | Express tn fp fn tp |
|---|---|---|---|---|---|---|---|---|
| A | 0.653 | 0.420 | 0.189 | 19 49 1 26 | 0.635 | 0.466 | 0.048 | 69 0 3 2 |
| B | 0.607 | 0.385 | 0.195 | 17 51 5 22 | 0.745 | 0.283 | 0.056 | 67 2 4 1 |
| C | 0.873 | 0.596 | 0.119 | 51 17 1 26 | 0.861 | 0.619 | 0.047 | 69 0 4 1 |
| D | 0.852 | 0.567 | 0.126 | 49 19 0 27 | 0.913 | 0.566 | 0.047 | 68 1 4 1 |
| E | 0.899 | 0.729 | 0.112 | 51 17 1 26 | 0.722 | 0.457 | 0.048 | 69 0 4 1 |

The Express held out rows hold only 5 deployers, so every Express figure above
rests on 5 positives and should be read that way.

## The fully fixed test

The test the last evaluation failed: set every failure count to zero, leave the
build result as recorded, and ask whether the estimate still separates projects
that deployed from ones that did not. Shown on all 675 rows and on the held out
rows alone.

React:

| | mean, deployed | mean, not deployed | ROC AUC | deployers clearing | non deployers clearing | held out ROC AUC | held out deployers clearing |
|---|---|---|---|---|---|---|---|
| A | 0.200 | 0.201 | 0.489 | 7 of 109 | 15 of 240 | 0.514 | 1 of 27 |
| B | 0.551 | 0.544 | 0.519 | 109 of 109 | 240 of 240 | 0.514 | 27 of 27 |
| C | 0.633 | 0.192 | 0.852 | 108 of 109 | 66 of 240 | 0.862 | 27 of 27 |
| D | 0.835 | 0.271 | 0.864 | 108 of 109 | 66 of 240 | 0.830 | 27 of 27 |
| E | 0.784 | 0.247 | 0.858 | 108 of 109 | 66 of 240 | 0.870 | 27 of 27 |

Express:

| | mean, deployed | mean, not deployed | ROC AUC | deployers clearing | non deployers clearing | held out ROC AUC | held out deployers clearing |
|---|---|---|---|---|---|---|---|
| A | 0.338 | 0.281 | 0.627 | 18 of 20 | 224 of 306 | 0.448 | 4 of 5 |
| B | 0.840 | 0.619 | 0.735 | 20 of 20 | 272 of 306 | 0.654 | 5 of 5 |
| C | 0.557 | 0.340 | 0.847 | 19 of 20 | 139 of 306 | 0.786 | 4 of 5 |
| D | 0.814 | 0.524 | 0.858 | 20 of 20 | 216 of 306 | 0.797 | 5 of 5 |
| E | 0.779 | 0.583 | 0.717 | 20 of 20 | 234 of 306 | 0.565 | 5 of 5 |

What this says, plainly:

- The constraint alone does not fix React. Model B moves every fully fixed
  React project to about 0.55, deployers and non deployers alike, so all of
  them clear. It removes the wrong direction and adds no separation.
- The build result is what separates React. Every model that has it, C, D
  and E, gives fully fixed React deployers a mean estimate between 0.63 and
  0.84 and non deployers one between 0.19 and 0.27.
- For Express, fixing the rules lifts deployers and most non deployers alike:
  under D, 216 of 306 fully fixed non deployers still clear. The model sorts
  Express projects well, a fully fixed ROC AUC of 0.858, but its operating
  point lets most fixed Express projects through.

## Whether each model moves the right way

The same check module 5.4 now runs on every model it trains: lower each
failure count by one, and turn each failed build into a successful one, on
every row. A change that lowers the estimate is a violation.

| | changes checked | lowered the estimate | largest fall |
|---|---|---|---|
| B | 7034 | 0 | 0 |
| C | 7281 | 2393 | 0.264 |
| D | 7281 | 0 | 0 |
| E | 7281 | 3234 | 0.182 |

Without the constraint, a third or more of all single fixes would have lowered
a project's estimate.

## What the constraint costs

On the held out rows E has the higher PR AUC, 0.710 against D's 0.565, at the
same ROC AUC. On the same five training folds, uncalibrated, E scores 0.713
with a spread of 0.047 and D scores 0.731 with a spread of 0.054, so the held
out gap comes from one split of 32 positives rather than from the constraint.
D was chosen because the gate needs a model that never lowers an estimate when
a project is fixed, and on the evidence it gives up nothing measurable for it.

Cross validated PR AUC of each parameter search, on the training rows:

| | chosen | defaults |
|---|---|---|
| B | 0.516, spread 0.075 | 0.464, spread 0.091 |
| C | 0.736, spread 0.027 | 0.689, spread 0.072 |
| D | 0.731, spread 0.054 | 0.722, spread 0.039 |

D's chosen parameters are learning rate 0.05, 200 iterations, 7 leaves, a
minimum of 20 rows per leaf and L2 regularisation 1.0.

## What the model relies on

Permutation importances for D on the held out rows, the fall in PR AUC when
one feature is shuffled, over 30 shuffles:

| feature | fall | spread |
|---|---|---|
| built | 0.309 | 0.035 |
| assessed_build | 0.051 | 0.065 |
| failed_secrets | 0.007 | 0.034 |
| failed_p1 | 0.005 | 0.009 |
| failed_structure | 0.002 | 0.005 |

Every other feature measured zero or less, within its spread. The lowest was
assessed_environment at minus 0.043 with a spread of 0.043.

The honest reading is that the estimate is mostly whether the project builds.
The audit's failure counts still move it, and by the constraint only ever
downward, but on the held out rows they add little measurable ranking. The
audit keeps its own role in the gate through the score and the blocker rule.

## Calibration

D, held out, predicted against observed deploy rate:

| estimate | rows | predicted | observed |
|---|---|---|---|
| 0.0 to 0.1 | 110 | 0.030 | 0.009 |
| 0.1 to 0.2 | 7 | 0.145 | 0.143 |
| 0.2 to 0.3 | 4 | 0.265 | 0.500 |
| 0.4 to 0.6 | 17 | 0.496 | 0.529 |
| 0.6 to 1.0 | 31 | 0.667 | 0.613 |

The band from 0.2 to 0.3 holds 4 rows and should not be read as a trend. The
highest estimate for any project is 0.802, and 0.887 with every failing rule
cleared, so no project reaches 0.9.

## Real fixed and broken projects

Each built for real by module builds. The React test fixtures have no
index.html, so Vite cannot build them, which is recorded as a real failure;
each also has a copy with the standard Vite entry page added, which builds.
D's operating threshold is 0.369.

| project | audit score | critical failures | builds | A | D |
|---|---|---|---|---|---|
| node_express_gated | 100 | 0 | yes | 0.342 | 0.842 |
| node_express_hardened | 69 | 2 | yes | 0.040 | 0.472 |
| node_express_insecure | 13 | 6 | yes | 0.028 | 0.111 |
| react_vite_ready | 99 | 0 | no | 0.066 | 0.047 |
| react_vite_ready with an entry page | 99 | 0 | yes | 0.066 | 0.851 |
| react_vite_hardened with an entry page | 43 | 3 | yes | 0.495 | 0.706 |
| react_vite_app with an entry page | 28 | 3 | yes | 0.279 | 0.595 |

Under A the fixed React project scored lowest of the three that build, 0.066
against 0.495 and 0.279. Under D it scores highest, 0.851 against 0.706 and
0.595. The two broken React projects still clear D's operating point; the
gate refuses them on their critical failures before the model is asked.

The last report's headline case, react_vite_ready refused at 0.055, was a
project that cannot build at all. D refuses it too, at 0.047, and now for a
reason the model can see.

## The decision

Fixed before any of these numbers existed: React is gated by the model on the
same terms as Express only if, with every failing rule cleared, the estimate
separates React deployers from non deployers with a ROC AUC of at least 0.70,
on all rows and on the held out rows, its held out ROC AUC within React is at
least 0.70, and at least 75 percent of fully fixed React deployers clear the
operating point.

| criterion | needed | D |
|---|---|---|
| fully fixed ROC AUC, all React rows | 0.70 | 0.864 |
| fully fixed ROC AUC, held out React rows | 0.70 | 0.830 |
| held out ROC AUC within React | 0.70 | 0.852 |
| fully fixed React deployers clearing | 75 percent | 108 of 109, 99 percent |

All four hold, so the hybrid gate applies to both stacks equally. Under the
old recipe, A, the same measures were 0.489, 0.514, 0.653 and 7 of 109.

## Caveats

- The builds ran a week after the deployments, against the npm registry as it
  was then. At least three rows built differently here than on Render: one
  React deployer failed locally on a Rollup native module, and two projects
  that failed here went live on Render and then failed their health checks.
- 39 rows were built on Node 24 because no local image carries the major
  they ask for. Without them, 636 rows, D scores a held out ROC AUC of 0.898
  and PR AUC of 0.572, a held out React ROC AUC of 0.843 and a fully fixed
  React ROC AUC of 0.853.
- For React the build result sits close to the mechanism that decides the
  label, because Render builds a static site in much the same way. It is still
  known before deployment, which is what makes it usable by the gate, but it
  is why React separates so well.
- The Express held out rows hold 5 deployers. D finds 1 of them at its
  operating point as audited, and all 20 Express deployers clear once fully
  fixed.
- The measures above come from the evaluation script. The promoted model is
  retrained by module 5.4 with the same split, seed and parameters, and its
  own figures are in `data/evaluation.json` and the artifact.

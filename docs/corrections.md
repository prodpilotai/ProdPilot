# Corrections to numbers in earlier commit messages

Pushed history is not rewritten. Where a commit message quoted a number that
later work showed to be wrong or out of date, the correction is recorded here.

## 7b50ae4 and 3a4394b describe a model that is no longer the one in use

Both commits were written on 2026-09-12 against the first model trained on
the full labelled dataset. After they were committed, a flaw was found in the
features: the 186 synthetic negatives had been audited as plain folders with no
git history while every real repository was a git clone, so
`assessed_git_hygiene` separated the two groups for a reason unrelated to
deploying. The negatives were re-audited with a one commit history and the
model was retrained and promoted. Commit 9ad9c79 corrected the training
module's docstring, but not these two messages.

Every number below comes from `data/evaluation.json` or from a direct
measurement of the promoted model, on the same 171 held out rows.

### 7b50ae4, Train a calibrated model on the full labelled dataset

| Quoted | As committed | Promoted model |
|---|---|---|
| Held out ROC AUC | 0.794 | 0.768 |
| Held out PR AUC | 0.450 | 0.427 |
| Operating threshold | 0.224 | 0.200 |
| Precision at the threshold | 0.391 | 0.346 |
| Recall at the threshold | 0.844 | 0.844 |
| F1 at the threshold | 0.535 | 0.491 |
| ROC AUC within React | 0.722 | 0.655 |
| ROC AUC within Express | 0.684 | 0.702 |
| Without is_node, ROC and PR AUC | 0.791 and 0.448 | 0.769 and 0.428 |
| Cross validated PR AUC, default then tuned | 0.507 then 0.514 | 0.506 then 0.525 |
| Brier, weighted against unweighted | 0.158 against 0.121 | 0.166 against 0.121 |

The committed claims about the probabilities being honest, and about the model
keeping most of its skill without the stack flag, still hold on the promoted
model. The claim that 27 of 32 deployers are found also still holds.

### 3a4394b, Use the model beside the audit score in the gate

| Quoted | As committed | Promoted model |
|---|---|---|
| Highest calibrated probability over all 684 projects | 0.81 | 0.838 |
| Highest with every failing rule set to passing | 0.63 | 0.515 |
| Fully fixed projects clearing the hybrid gate | 81 percent | 44 percent, 299 of 684 |
| of them, React | 87 percent | 4 percent, 15 of 357 |
| of them, Express | 75 percent | 87 percent, 284 of 327 |
| react_vite_ready as a git repository | opened at 31 percent against 22 | refused at 5.5 percent against 20 |
| assessed_git_hygiene | among the strongest features | 17th of 25, importance 0.016 |

Two figures in that message were not re-measured on the promoted model and
should not be relied on: the percentile design's 54 percent of fully fixed
projects and its 6 of 32 deployers, and the 6 percent estimate for
react_vite_ready without git history.

The decision that message records, gating on the audit score and the model
together rather than on the probability at 0.9, still stands: no project
reaches 0.9 on the promoted model either. What does not stand is its evidence
that the hybrid works for React. On the promoted model it does not, which is
why the build success work that follows was done.

## Where the current numbers are

The commits made after these corrections, starting with the one that adds this
file, carry the current measurements: the build success feature, the
monotonically constrained model, its evaluation by stack, and the gate policy
decided from that evaluation.

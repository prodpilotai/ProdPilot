# Deployability model: baselines, uncertainty and sensitivity

Produced by `python -m prodpilot.modelstats`. Same 675 rows, same split
(506 training, 169 held out) and seed as module 5.4. Threshold 0.3686, reproduced from out-of-fold predictions as 0.3686.

## Held out rows, with 95% bootstrap intervals

| Predictor | Cut | ROC AUC | 95% CI | PR AUC | 95% CI | Precision | Recall |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Model D (promoted) | 0.369 | 0.909 | 0.867 to 0.946 | 0.565 | 0.464 to 0.729 | 0.583 | 0.875 |
| Model E | 0.447 | 0.913 | 0.869 to 0.954 | 0.710 | 0.594 to 0.835 | 0.614 | 0.844 |
| Build and stack only | 0.591 | 0.886 | 0.853 to 0.916 | 0.533 | 0.460 to 0.621 | 0.575 | 0.844 |
| Audit score alone | 29.000 | 0.706 | 0.634 to 0.776 | 0.293 | 0.248 to 0.402 | 0.296 | 0.656 |
| Rule-only gate | 0.500 | 0.493 | 0.482 to 0.500 | 0.189 | 0.189 to 0.189 | 0.000 | 0.000 |

### react_vite

| Predictor | ROC AUC | 95% CI | PR AUC | 95% CI | Precision | Recall | tn fp fn tp |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Model D (promoted) | 0.852 | 0.769 to 0.919 | 0.567 | 0.458 to 0.748 | 0.587 | 1.000 | 49 19 0 27 |
| Model E | 0.899 | 0.833 to 0.952 | 0.729 | 0.601 to 0.865 | 0.605 | 0.963 | 51 17 1 26 |
| Build and stack only | 0.853 | 0.794 to 0.904 | 0.575 | 0.491 to 0.675 | 0.575 | 1.000 | 48 20 0 27 |
| Audit score alone | 0.518 | 0.398 to 0.636 | 0.292 | 0.253 to 0.399 | 0.294 | 0.741 | 20 48 7 20 |
| Rule-only gate | 0.485 | 0.463 to 0.500 | 0.284 | 0.284 to 0.284 | 0.000 | 0.000 | 66 2 27 0 |

### node_express

| Predictor | ROC AUC | 95% CI | PR AUC | 95% CI | Precision | Recall | tn fp fn tp |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Model D (promoted) | 0.913 | 0.785 to 0.994 | 0.566 | 0.249 to 0.933 | 0.500 | 0.200 | 68 1 4 1 |
| Model E | 0.722 | 0.394 to 0.986 | 0.457 | 0.116 to 0.863 | 1.000 | 0.200 | 69 0 4 1 |
| Build and stack only | 0.623 | 0.573 to 0.674 | 0.088 | 0.078 to 0.100 | 0.000 | 0.000 | 69 0 5 0 |
| Audit score alone | 0.842 | 0.677 to 0.964 | 0.389 | 0.139 to 0.763 | 0.333 | 0.200 | 67 2 4 1 |
| Rule-only gate | 0.500 | 0.500 to 0.500 | 0.068 | 0.068 to 0.068 | 0.000 | 0.000 | 69 0 5 0 |

## Model D against model E, paired bootstrap

| Measure | Mean D minus E | 95% CI | Share of draws where D is higher |
| --- | --- | --- | --- |
| roc_auc | -0.004 | -0.037 to 0.030 | 0.394 |
| pr_auc | -0.131 | -0.242 to -0.005 | 0.019 |

## Repeated cross validation, 10 repeats of 5 folds over all rows

| Model | Measure | Mean | SD |
| --- | --- | --- | --- |
| D | roc_auc | 0.923 | 0.028 |
| D | pr_auc | 0.685 | 0.074 |
| D | brier | 0.080 | 0.012 |
| D | react_vite_roc_auc | 0.875 | 0.038 |
| D | react_vite_pr_auc | 0.682 | 0.074 |
| D | node_express_roc_auc | 0.867 | 0.123 |
| D | node_express_pr_auc | 0.612 | 0.211 |
| E | roc_auc | 0.919 | 0.025 |
| E | pr_auc | 0.693 | 0.077 |
| E | brier | 0.081 | 0.011 |
| E | react_vite_roc_auc | 0.883 | 0.034 |
| E | react_vite_pr_auc | 0.702 | 0.083 |
| E | node_express_roc_auc | 0.817 | 0.133 |
| E | node_express_pr_auc | 0.563 | 0.224 |

## Fully fixed projects clearing the threshold

| Group | All rows | Held out |
| --- | --- | --- |
| react_vite_deployers | 108 of 109 | 27 of 27 |
| react_vite_non_deployers | 66 of 240 | 20 of 68 |
| node_express_deployers | 20 of 20 | 5 of 5 |
| node_express_non_deployers | 216 of 306 | 48 of 69 |

## Threshold sensitivity, model D on held out rows

| Threshold | Precision | Recall | Express precision | Express recall | React precision | React recall |
| --- | --- | --- | --- | --- | --- | --- |
| 0.2 | 0.577 | 0.938 | 0.600 | 0.600 | 0.575 | 1.000 |
| 0.3 | 0.583 | 0.875 | 0.500 | 0.200 | 0.587 | 1.000 |
| 0.369 | 0.583 | 0.875 | 0.500 | 0.200 | 0.587 | 1.000 |
| 0.45 | 0.561 | 0.719 | 0.500 | 0.200 | 0.564 | 0.815 |
| 0.5 | 0.561 | 0.719 | 0.500 | 0.200 | 0.564 | 0.815 |

F1-best threshold within each stack, from out-of-fold predictions on the training rows (information only): react_vite 0.3686, node_express 0.4723.

## Priority weights of the audit score

Real repositories in the matrix matched to their audit record: 494 of 494; the released audit reproduces the score stored with the features on 429. Synthetic negatives are excluded: their working copies carry no Git history.

| Scheme | Rows passing 90 | Decisions that change |
| --- | --- | --- |
| halving (32 to 1, in use) | 0 | |
| equal | 0 | 0 |
| linear (6 to 1) | 0 | 0 |

The 20 scored samples at the gate: equal weights change 3 pass-mark decisions (amb_two_drivers, node_express_insecure, react_vite_ready+entry), linear weights change 1 (amb_two_drivers).

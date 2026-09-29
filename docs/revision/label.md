# What the deployment label measures

Produced by `python -m prodpilot.labelstats`. All results are correlational.

Real repositories with an audit and a label: 498, deploy rate 19.1%. Synthetic negatives: 186, deploy rate 18.3%.

## Rules and domains whose failure changes the deploy rate

Rate among repositories failing it against those passing it, with a Newcombe 95% interval on the difference. Listed where the interval excludes zero.

| Stack | Rule or domain | Deployed when failing | Deployed when passing | Difference | 95% CI | Holds after Bonferroni |
| --- | --- | --- | --- | --- | --- | --- |
| react_vite | git_hygiene | 73 of 215 | 14 of 24 | -0.244 | -0.426 to -0.038 | no |
| node_express | API-001 | 7 of 228 | 1 of 5 | -0.169 | -0.594 to -0.003 | no |
| node_express | BLD-001 | 3 of 223 | 5 of 36 | -0.125 | -0.273 to -0.043 | yes |
| node_express | BLD-004 | 1 of 142 | 7 of 117 | -0.053 | -0.112 to -0.009 | no |
| node_express | OBS-001 | 4 of 165 | 3 of 7 | -0.404 | -0.726 to -0.132 | yes |
| node_express | SEC-001 | 6 of 252 | 2 of 7 | -0.262 | -0.618 to -0.057 | yes |
| react_vite | BLD-011 | 0 of 16 | 87 of 223 | -0.390 | -0.456 to -0.187 | no |
| react_vite | GIT-007 | 69 of 211 | 18 of 28 | -0.316 | -0.477 to -0.120 | no |

Rules tested: 50 rule and stack pairs; 7 show a difference whose interval excludes zero, against about 2.5 expected by chance at 95%. Domains tested: 16; 1 excluding zero.

## Synthetic negatives against their source repository

Paired: 163. Outcome unchanged: 153 (both failed 129, both deployed 24). Source deployed but negative failed: 10. Source failed but negative deployed: 0.

| Broken rule | Pairs | Source deployed | Deployed to failed | Failed to deployed |
| --- | --- | --- | --- | --- |
| BLD-001 | 2 | 0 | 0 | 0 |
| BLD-002 | 1 | 0 | 0 | 0 |
| BLD-003 | 5 | 0 | 0 | 0 |
| BLD-004 | 6 | 0 | 0 | 0 |
| BLD-005 | 3 | 0 | 0 | 0 |
| BLD-007 | 1 | 0 | 0 | 0 |
| BLD-008 | 3 | 1 | 0 | 0 |
| BLD-010 | 10 | 2 | 0 | 0 |
| BLD-011 | 29 | 9 | 9 | 0 |
| BLD-013 | 1 | 0 | 0 | 0 |
| GIT-002 | 19 | 1 | 0 | 0 |
| GIT-005 | 21 | 5 | 0 | 0 |
| GIT-006 | 19 | 5 | 0 | 0 |
| SCR-002 | 20 | 1 | 0 | 0 |
| SCR-003 | 1 | 1 | 0 | 0 |
| SCR-004 | 22 | 9 | 1 | 0 |

## Built locally but did not deploy: where the deploy stopped

Render build and runtime logs were not stored at labelling time, and every service was deleted after labelling, so causes are reported by stage.

| Group | Where it stopped | Projects |
| --- | --- | --- |
| node_express_negative | Render reported the deploy failed | 36 |
| node_express_negative | went live, health path never answered | 4 |
| node_express_negative | timed out while Render was building | 1 |
| node_express_real | Render reported the deploy failed | 151 |
| node_express_real | went live, health path never answered | 34 |
| node_express_real | timed out while Render was building | 8 |
| react_vite_negative | Render reported the deploy failed | 16 |
| react_vite_negative | went live, health path never answered | 4 |
| react_vite_real | Render reported the deploy failed | 37 |
| react_vite_real | went live, health path never answered | 9 |

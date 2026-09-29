# Run time per stage

Produced by `python tests/timing.py`.

| Stage | Samples | Median s | Min s | Max s |
| --- | --- | --- | --- | --- |
| Audit | 22 | 0.16 | 0.0 | 0.42 |
| One repair cycle, with the regression guard | 18 | 3.23 | 0.72 | 11.94 |
| Whole chain, recorded run | 23 | 16.0 | 0.4 | 58.1 |

Gate build and ProdPush stage 3 need Docker; they are inside the whole-chain times recorded by tests/pipeline.py.

| Sample | Audit s | Failing rules | Cycle s | Contracts |
| --- | --- | --- | --- | --- |
| amb_two_drivers | 0.095 | 21 | 3.851 | 24 |
| amb_two_entries | 0.278 | 16 | 9.017 | 29 |
| amb_two_lockfiles | 0.167 | 16 | 2.9 | 17 |
| amb_two_roots | 0.148 | 17 | 3.364 | 20 |
| amb_unnamed_secret | 0.148 | 22 | 4.82 | 27 |
| ambiguous_fullstack | 0.002 | 0 |  |  |
| docker_ok | 0.16 | 7 | 1.344 | 9 |
| node_express_api | 0.139 | 20 | 4.472 | 24 |
| node_express_gated | 0.229 | 0 |  |  |
| node_express_hardened | 0.419 | 8 | 3.321 | 12 |
| node_express_insecure | 0.375 | 22 | 11.94 | 32 |
| node_express_ready | 0.131 | 9 | 1.833 | 8 |
| node_express_secrets | 0.104 | 12 | 1.88 | 17 |
| node_express_secure | 0.373 | 15 | 8.158 | 19 |
| node_express_wildcard | 0.153 | 21 | 4.168 | 27 |
| react_vite_app | 0.166 | 15 | 2.115 | 23 |
| react_vite_hardened | 0.312 | 14 | 3.144 | 20 |
| react_vite_noroute | 0.17 | 15 | 2.2 | 23 |
| react_vite_ready | 0.2 | 1 | 0.724 | 3 |
| react_vite_ts_config | 0.078 | 14 | 0.723 | 20 |
| unrecognized_python_service | 0.002 | 0 |  |  |
| vite_without_react | 0.002 | 0 |  |  |

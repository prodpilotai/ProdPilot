# Response to the reviewer

Manuscript 1fbb182b-3769-4797-bc8c-dd30292afedd, "ProdPilot: A Layered Model
Context Protocol System for Automated Production Readiness and Deployment of
Vibe-Coded Projects", submitted to Computing.

We thank the reviewer for a careful and constructive report. Every number
added in this revision is produced by a script in the ProdPilot repository and
recorded in `docs/revision/`, and each response below names the section of the
revised manuscript where the change appears. Where we could not fully resolve a
point, we say so.

## Major comments

**1. The evaluation does not compare an agent working without ProdPilot, and
the delegated class and self-report were measured only with a stand-in
executor.**

We measured the delegated class with live agents: ten trials covering all six
delegated rules, with Cursor driving Grok 4.6 and Claude Code driving Claude
Opus 5, all resolved on the first attempt within the contract's boundary and
without regressions, with every agent's report agreeing with the checker
(Section 6.4, p. 14, Table 7). We did not run the two-arm comparison in this round. We
built its harness, which prepares an agent-alone arm and a ProdPilot arm on the
same findings and scores fixes, regressions, overclaims and run-to-run variance
from disk, and we release it with the artefact. Accordingly we narrowed RQ1 and
RQ3 to what the evidence shows: the 25 rejected reports come from insufficient
contracts, not from an agent overstating success (Sections 6.5 and 6.11, pp. 15 and 19), and
the comparison is stated as future work in the abstract, threats and conclusion.

**2. RQ2 and RQ4 were left unanswered although the data existed.**

Both are now answered. For RQ2 we audited all 499 auditable repositories: of
7,407 violations, 93.5% carry a static or parametric fix, 99.4% weighted by
priority, 94.9% within Express and 91.3% within React (Section 6.2, p. 12, Fig. 3).
Running extraction on every parametric instance gives a real-world refusal rate
of 40.6%, which we break down: 21.8% are preconditions that the loop's ordering
resolves, 5.9% are genuine ambiguity against 20.3% on our deliberately
ambiguous samples, and 5.6% expose disagreement between a checker and its
extractor, which we report as a limitation of ProdPilot. For RQ4 we scored the
audit score and the rule-only gate as predictors on the same held-out rows: the
gate admits two projects, neither of which deployed, and the audit score
reaches 0.706 ROC AUC, against 0.909 for the model (Section 6.6, p. 15, Table 8).

**3. The model largely reduces to the build feature, the Express results rest
on five positives, and there are no confidence intervals.**

We agree and now show it directly. A build-and-stack logistic regression
reaches 0.886 ROC AUC against the model's 0.909, and within React it matches the
model; within Express the audit score carries the signal (0.842 against 0.623
for build and stack). We added 95% stratified bootstrap intervals for every
predictor overall and per stack (Table 8, Fig. 4), ten repeats of five-fold
cross-validation, per-stack confusion counts, the counterfactual clearing rate
of fully fixed projects (216 of 306 Express non-deployers), and a threshold
sweep showing that a stack-specific threshold would not raise Express recall.
We removed "equal terms" and moved the React near-leakage discussion into
Section 6.6. Model D versus E is now compared on the same bootstrap footing and
by repeated cross-validation, where the two are level.

**4. The label measures startability on Render rather than production
readiness.**

We agree, and now call the target deployability on Render throughout (Sections
4.4 and 6.7, pp. 10 and 17). Pairing each of the 163 synthetic negatives made from a real
repository with its source shows that breaking a rule changed the outcome in 10
of the 34 pairs whose source deployed, 9 of them by removing the React build
script. Per-rule analysis finds 7 of 50 rules associated with deployment
against 2.5 expected by chance, 3 surviving Bonferroni correction. For projects
that built but did not deploy, we report where the deploy stopped. Render's logs
were not stored at labelling time and the services were deleted, so a finer
categorisation needs a sampled redeployment, which we have prepared but not run
in this round.

**5. Implementation statements did not match the released artefact.**

All five are corrected (Sections 5 and 6.9, pp. 11 and 18). The two live redeployments used
the earlier workflow with a fixed 90-second wait, and the wait-for-deploy
workflow is now described as implemented and tested but not yet run live. The
test suite is 1,799 tests on Linux, macOS and Windows with Python 3.11 to 3.14,
and the version is 1.0.0. The 11,988 bytes is the whole MCP result for the
Express blueprint, text plus structured content, measured over a live stdio
session; the 10.9 KB was the VS Code client's own figure for what it saved. On
Node.js, generated Dockerfiles fall back to Node 20 when a project declares no
version; we state its end of life and that the fallback should move, and report
the Node images the build pass actually used.

**6. Related work misses the closest prior art, and the novelty claim needs
recalibrating.**

We added Sorald, SpongeBugs, CodeCureAgent, Patcas and Motogna, Rosa et al., the
Docker smell repair study of Zhang et al., IaCGen, and the MCP studies of Hou et
al. and Hasan et al., each verified against its DOI (Section 3, pp. 4 to 7, Tables 1 and 2).
We restated the contribution as the combination the reviewer describes, replaced
"No existing tool covers the path" with a claim Table 1 supports, and discuss
MCP-specific risks in Section 2 (p. 3).

**7. The manuscript is not in Computing's one-column template.**

The manuscript is now in the Springer Nature template (sn-jnl, one column,
numbered references with DOI links) and runs to 24 pages, references included. We condensed the
background, merged Tables II and III into one table, removed Fig. 3, Table IX
and Fig. 15, replaced Fig. 16 with a table, and use six keywords.

**8. Declarations and data and code availability are missing.**

A Declarations section now covers funding, competing interests, ethics, data
availability, code availability, author contributions and the use of AI tools.
The dataset manifest with commits, licences, labels, build results and split,
the feature matrix, the ruleset, the model and the analysis scripts form a
release bundle to be deposited with a DOI.

## Minor comments

1. **"Runs no model."** Now "runs no generative model"; the gate's classifier is
   described as such (Abstract, Section 1, p. 2).
2. **498 versus 499.** Table 5 (p. 13) gives the full chain: 500 kept, 499 audited (one
   repository exceeds the parser's recursion limit), 498 labelled (Render refused
   one service), 494 real repositories in the model's matrix (four builds could
   not be determined).
3. **Units in Figs. 5 and 6.** Table 6 (p. 14) follows static, parametric and delegated
   instances from contracts sent to contracts applied, confirmed and passing,
   and defines instance and contract.
4. **Which run each number comes from.** Section 6.1 states that figures come
   from the final full-chain run unless marked as the second run.
5. **Operating point criterion.** Section 6.6 explains that F1 weighs the two
   errors equally, reports a threshold sweep, and states which way the cut should
   move for a stricter or a more permissive gate.
6. **Weights and the 90 cut-off.** Section 4.2 gives the rationale: each tier
   outweighs all lower tiers together. Section 6.6 reports that none of the 494 real repositories in the matrix reaches 90 under halving, equal or linear weights, so no decision on them changes, while at the gate equal weights would change 3 of 20 sample decisions and linear weights 1.
7. **Pseudocode.** Algorithm 1 gives the loop and the gate.
8. **RQ5 wording.** Now "without IDE-specific server code", with its limits
   stated (Section 6.11).
9. **Ethics of the third-party deployments.** Section 5 and the Declarations
   cover licences, pacing, public reachability, deletion and the handling of
   detected credentials.
10. **Threats to validity.** Expanded as suggested (Section 6.12).
11. **The five percent statistic.** Now attributed explicitly to Barbosa et al.
    as their report; we did not find the primary source it rests on.
12. **DOIs and foundational references.** Every reference carries its DOI or a
    stable publisher link. We added Friedman, Pedregosa et al., Breiman, Shannon
    and the MCP specification. Platt's 1999 chapter has no DOI record, so we cite
    Lin, Lin and Weng's journal article on Platt's method.
13. **Background length.** Condensed to one short section (Section 2).
14. **Tables II and III.** Merged into Table 2.
15. **Fig. 3, Table IX and Fig. 16.** Fig. 3 and Table IX are removed; Fig. 16
    is now Table 10, which reads in greyscale.
16. **Run time per stage.** Table 9 (Section 6.8, p. 18) gives the median and range per stage: 0.16 s for the audit, 3.2 s for one repair cycle with the regression guard, 16.0 s for the whole chain with the Docker build, and 82 s for the live deployment. The Docker-dependent gate build and container stage are not separated from the whole-chain time.
17. **Title.** We kept the title and would welcome the editor's view on the
    alternatives we considered, which name the scope or say "AI-generated".
18. **Acknowledgements and contributions.** An acknowledgements section and a
    CRediT statement are included.

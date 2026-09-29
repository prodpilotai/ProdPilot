# Live-agent comparison: session protocol

The harness is `tests/agents.py`. It prepares each session, prints the text to
paste into the agent, and scores the result from the files on disk. A person
drives the agent; everything else is measured.

## Design

Two arms, the same agent and the same model setting in both:

- **A, agent alone.** The project plus `ISSUES.md`, which lists every failing
  rule in plain language: what is required, what was found and where. ProdPilot
  is not connected.
- **B, with ProdPilot.** The project with ProdPilot connected. The agent is given
  the failing rule identifiers and uses `prodpilot_fix_instruction` and
  `prodpilot_fix_applied`. It is told not to call `prodpilot_deploy`.

Both arms see the same findings, so the comparison isolates the contracts and
the verification, not the information. Arm B cannot be told to "audit and fix
until the gate decides": the server offers no audit or gate tool, and the
deploy tool, which runs the gate, creates real infrastructure. The scorer
applies the gate's audit conditions (no failing P0 rule, score of at least 90)
afterwards instead.

Eight samples, chosen in `tests/agents.py`: the six whose audit shows a
delegated rule (node_express_insecure, react_vite_app, react_vite_noroute,
react_vite_ready, amb_unnamed_secret, node_express_secrets) and two Express
samples with static and parametric failures only (node_express_api,
node_express_wildcard).

Runs: three per sample per arm in VS Code with Copilot, 48 sessions. Cursor is
optional, on four samples with two runs each.

## Before the first session

1. Install ProdPilot and run `prodpilot connect vscode`.
2. Pick one model in the Copilot chat model picker and use it for every
   session. Write its exact name down.
3. Decide how to switch arms. For arm A, open **MCP: List Servers** and stop the
   prodpilot server, then confirm the agent's tool list does not show it. For
   arm B, start it again and confirm the five tools are listed.

## Each session

All commands run from the ProdPilot repository root.

1. Prepare the workspace, for example sample `react_vite_app`, arm `B`, run `1`:

       python tests/agents.py prep react_vite_app B 1

   It prints the workspace path and the exact text to paste.
2. In VS Code, **File, Open Folder** on that workspace, in a new window. Open a
   new agent chat, so nothing carries over from another session.
3. Set the arm as described above. Check the model picker.
4. Mark the start, then paste the printed text into the chat:

       python tests/agents.py mark E:\tmp\agents\react_vite_app\B\run1 start

5. Let the agent work. Approve its tool calls and file edits when asked; do not
   type anything else, and do not hint at a fix. If the agent asks a question,
   answer only "Use your judgement."
6. When the agent stops, copy its final message, including the FIXED and NOT
   FIXED lines, into `agent_report.txt` in the workspace root. Then mark the end:

       python tests/agents.py mark E:\tmp\agents\react_vite_app\B\run1 end

7. Score it:

       python tests/agents.py score E:\tmp\agents\react_vite_app\B\run1

## Time budget

20 minutes per session. If the agent is still working at 20 minutes, stop it,
save whatever final message exists (or an empty file), mark the end and score
it. The scorer records a session over 20 minutes as a timeout.

## Order

Alternate arms rather than doing all of one arm first, so any change in the
model service over the day affects both arms alike: for each sample, run A1,
B1, A2, B2, A3, B3.

## When all sessions are scored

    python tests/agents.py summary --out docs/revision

This writes `agent.json` and `agent.md`: pass rate per fix type per arm,
regressions, the overclaim rate (rules the agent reported fixed that still
fail), sessions meeting the gate's audit conditions, timeouts, and run-to-run
variance, the share of changed files whose content, with whitespace ignored,
is identical across a sample's runs.

## What to record by hand

- The VS Code and Copilot Chat versions, from **Help, About**.
- The exact model name from the picker.
- The date of each session.
- Anything unusual: a refusal, an error, an agent that edited outside the
  workspace.

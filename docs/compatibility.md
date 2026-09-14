# IDE compatibility record

Phase 7 from Section 8 of the Phased Implementation Plan. Module 7.1 validates
VS Code, module 7.2 validates Cursor and Windsurf. Section 8 of the Complete
Solution Document asks for the results to be recorded as observed, including
any client on which the system does not work, and that is the only standard
used here.

## How to read the matrix

Every cell is either a result that was observed, with the date, or "Not
tested" with the reason. Nothing is marked from documentation alone.

| Column | What it records |
| --- | --- |
| Discovery | The client reads its config file and starts the server |
| Tool listing | All five tools appear in the client |
| Content form | A STATIC or DYNAMIC-PARAMETRIC fix instruction is invoked and the contract comes back correct |
| Constraint form | A DYNAMIC-DELEGATED fix instruction is invoked and the contract comes back correct |
| Per-call approval | Whether each tool call needs the developer's confirmation by default |
| Response size | Whether the largest response, 11,988 bytes, reaches the agent whole |

## Compatibility matrix

| Client | Discovery | Tool listing | Content form | Constraint form | Per-call approval | Response size |
| --- | --- | --- | --- | --- | --- | --- |
| Direct stdio client, `.vscode/mcp.json` command | Pass, 14 Sep | Pass, 5 of 5, 14 Sep | Pass, SEC-002, 14 Sep | Pass, STR-003, 14 Sep | Not applicable, no person in the loop | Pass, arrives whole, 14 Sep |
| VS Code, Copilot agent mode | Pass on VS Code 1.134.0, 21 Aug; not re-observed on 1.137.0 | Not tested: Copilot quota not confirmed | Not tested: Copilot quota not confirmed | Not tested: Copilot quota not confirmed | Not tested: Copilot quota not confirmed | Not tested: Copilot quota not confirmed |
| Cursor | Not tested: module 7.2 | Not tested: module 7.2 | Not tested: module 7.2 | Not tested: module 7.2 | Not tested: module 7.2 | Not tested: module 7.2 |
| Windsurf | Not tested: module 7.2 | Not tested: module 7.2 | Not tested: module 7.2 | Not tested: module 7.2 | Not tested: module 7.2 | Not tested: module 7.2 |

## Module 7.1: VS Code

Recorded 14 September 2026 on Windows 11, Python 3.11.15, VS Code 1.137.0
with its built-in GitHub Copilot Chat 0.65.0, against commit `2457548`.

### The transport, for every tool

The server was started with the exact command `.vscode/mcp.json` names,
`${workspaceFolder}/.venv/Scripts/prodpilot.exe serve`, and driven by a real
MCP client session. The handshake returned `prodpilot 0.1.0` on protocol
`2025-11-25`, and tool discovery returned all five tools the server has today.

| Tool | Phase | Called with | Observed |
| --- | --- | --- | --- |
| `prodpilot_ping` | 1 | no arguments | Fixed status payload, transport `stdio` |
| `prodpilot_detect_stack` | 1 | `node_express_insecure` | Stack `node_express`, blueprint of 28 items |
| `prodpilot_fix_instruction` | 3 | `node_express_insecure`, SEC-002 | STATIC, content form, `insert_after` in `src/server.js` |
| `prodpilot_fix_instruction` | 3 | `react_vite_ready`, STR-003 | DYNAMIC-DELEGATED, constraint form, `author_within_constraint` with requirement, boundary and forbidden list, no content |
| `prodpilot_fix_applied` | 3 | `react_vite_ready`, STR-003, claimed applied | `unresolved`, verified false: the checker, not the claim, decided |
| `prodpilot_deploy` | 6 | `node_express_insecure` | Stopped at the scoring gate, score 13 with 6 critical failures; the 8 later stages did not run and nothing reached GitHub or Render |

`tests/test_clients.py` repeats all of this on every suite run, reading the
launch command from the config file itself, so a config that drifts from what
works fails the suite.

### Which response is largest

Section 8 asks for the size limit to be tested on the largest real response.
No tool returns a full audit report, so the candidates were measured rather
than assumed: every tool on every sample project, every failing rule, 568
calls over the real transport, none an error. The deploy tool was only given
projects scoring under the gate threshold.

| Tool | Calls | Largest result on the wire | Case |
| --- | --- | --- | --- |
| `prodpilot_detect_stack` | 22 | 11,988 bytes | Any Node.js with Express project, the 28 item blueprint |
| `prodpilot_fix_instruction` | 264 | 3,051 bytes | `node_express_insecure`, STR-001 |
| `prodpilot_deploy` | 17 | 2,763 bytes | `amb_unnamed_secret`, stopped at the gate |
| `prodpilot_fix_applied` | 264 | 2,289 bytes | `amb_two_entries`, API-001 |
| `prodpilot_ping` | 1 | 505 bytes | |

The result carries the payload twice, once as text and once as structured
content, so the 11,988 bytes hold a 6,431 byte payload. The Node.js with
Express blueprint is the test case for the size limit.

### What the documentation settles, and what it does not

Neither week-one item can be answered from documentation, so both remain for
observation in the client.

Per-call approval. The VS Code MCP page says "You might be asked to confirm
each tool invocation." The approvals page lists the choices a confirmation
offers, a single use or approval for the session, the workspace or all future
invocations, and the settings `chat.tools.global.autoApprove` and
`chat.tools.eligibleForAutoApproval`. Neither page states the default for an
MCP tool. This machine's VS Code settings set neither option, so an observed
confirmation would be the default behaviour.

Response size. VS Code documents no limit on a tool result. An open VS Code
issue, microsoft/vscode#311068, reports MCP tool results between 50 KB and
999 KB being truncated on VS Code 1.116.0, with a community report of
truncation at 14.4 KB. ProdPilot's largest result, 11,988 bytes, is below both
figures, which suggests it arrives whole but does not show it.

### The Copilot quota

Phase 1 could not invoke from agent mode because the plan's monthly allowance
was spent; the Copilot Chat log then recorded `402 quota_exceeded`. On 14
September the Copilot Chat log shows the account signed in on the
`free_educational_quota` plan with no quota error, but no chat request was made
that day, so the log cannot show whether the allowance has reset. The GitHub
CLI on this machine is not signed in, so the quota could not be read from the
account either. The blocker is recorded as not confirmed resolved.

### To close VS Code: steps for a person at the keyboard

Do these in order and write down what happens at each step, including a
failure. Never let the agent call `prodpilot_deploy` on `node_express_gated`:
that sample passes the gate and a deploy would create a real service.

1. Click the Copilot icon in the status bar and read the remaining chat and
   premium request allowance. If it is spent, stop here and note the reset date
   shown.
2. In the Extensions view, confirm GitHub Copilot Chat is installed, enabled
   and signed in. Note its version and the VS Code version from Help, About.
3. Open `E:\ProdPilot\ProdPilot` itself as the workspace folder, not its parent.
4. Run Chat: Manage Tool Approval and confirm no ProdPilot tool is already
   approved. Clear any approval found, so the default is what gets observed.
5. Run MCP: List Servers, select `prodpilot` and start it. Note whether a trust
   prompt appears and accept it. Starting from the `mcp.json` editor skips the
   prompt, so use the command.
6. Open the chat view in Agent mode, open the tools picker and confirm the five
   `prodpilot_` tools are listed.
7. Content form. Ask: "Call prodpilot_fix_instruction with project_path
   E:\ProdPilot\ProdPilot\tests\samples\node_express_insecure and rule_id
   SEC-002, and show me the contract. Do not edit any file." Note whether a
   confirmation appears and which choices it offers, choose to allow once, and
   check the answer: STATIC, form content, `insert_after`, `src/server.js`.
8. Repeat step 7 with rule_id BLD-001. Note whether the confirmation appears
   again, which shows whether approval is per call or per tool.
9. Constraint form. Ask the same for project_path
   `E:\ProdPilot\ProdPilot\tests\samples\react_vite_ready` and rule_id STR-003.
   Note the confirmation and check the answer: DYNAMIC-DELEGATED, form
   constraint, `author_within_constraint`, with a requirement, a boundary and a
   forbidden list.
10. Response size. Ask: "Call prodpilot_detect_stack on
    E:\ProdPilot\ProdPilot\tests\samples\node_express_insecure and tell me the
    item_count and how many entries each of required_files, required_configs
    and required_code_patterns has." Expand the tool call in the chat and note
    whether the result is shown whole, truncated, or saved as a resource. The
    correct answer is 28: 5 files, 6 configs, 17 code patterns.
11. Run `git status` in the repository and confirm nothing under
    `tests/samples` changed. If the agent edited anything, restore it with
    `git checkout -- tests/samples`.

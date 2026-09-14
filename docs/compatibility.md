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
| Cursor 3.20.17, Agent | Pass after the developer enabled it, 14 Sep | Pass, 5 of 5, 14 Sep | Pass, SEC-002 and BLD-001, 14 Sep | Pass, STR-003, 14 Sep | No prompt on any of 3 calls, against Cursor's documented default, 14 Sep | Pass, 28 of 28 items counted, 14 Sep |
| Windsurf, now Devin 3.10.23, agent | Fails from the committed configs, `${workspaceFolder}` is blanked; pass from Devin's own `.devin/mcp_config.local.json`, 14 Sep | Pass, 5 of 5, 14 Sep | Pass, SEC-002 and BLD-001, 14 Sep | Pass, STR-003, 14 Sep | Prompts on every call, each approval covers one call, 14 Sep | Pass, all 28 items listed, 14 Sep |

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

## Module 7.2: Cursor and Windsurf

Recorded 14 September 2026 on the same machine and commit as module 7.1.
Both IDEs were installed that day for this module. Each was configured by its
own documented method, not by assuming it matches VS Code's.

### Cursor 3.20.17

Configuration. Cursor reads a project's servers from `.cursor/mcp.json`, under
the key `mcpServers`, and resolves `${workspaceFolder}` in `command` and
`args`. The file committed here names the same launch command as
`.vscode/mcp.json`, and `tests/test_clients.py` proves that command over the
real transport on every suite run, alongside the VS Code one.

Discovery. Cursor found the file as soon as it was written and registered the
server as `project-0-ProdPilot-prodpilot`, but held it disconnected until the
developer switched it on in Cursor's MCP settings, which matches its
documented handling of a newly added project server. Once enabled, it started
the server and logged `tools=5, status=connected`.

Every result below was read from two places: the agent's answer in the chat,
and ProdPilot's own log of each call, which Cursor keeps per server.

| Check | Asked | Observed |
| --- | --- | --- |
| Content form | `node_express_insecure`, SEC-002 | STATIC, content, `insert_after` in `src/server.js`, helmet `^8.1.0`; call logged 10:56:52 |
| Content form, repeated | `node_express_insecure`, BLD-001 | DYNAMIC-PARAMETRIC, content, `create_file` for `Dockerfile`; call logged 10:58:29 |
| Constraint form | `react_vite_ready`, STR-003 | DYNAMIC-DELEGATED, constraint, `author_within_constraint` with requirement, boundary and forbidden list, no content; call logged 11:00:44 |
| Response size | `prodpilot_detect_stack`, `node_express_insecure` | The agent counted 28 items, 5 files, 6 configs and 17 code patterns, which needs the last and largest list of the 11,988 byte result; no truncation in Cursor's logs; call logged 11:03:24 |

No file under `tests/samples` changed.

Per-call approval. None of the three fix instruction calls stopped for
approval; each ran as soon as the agent chose to call it. Cursor's
documentation says it asks for approval before using MCP tools by default.
This installation's stored settings show why the two differ: on a new install
Cursor applied its own default, recorded as `smartModeAutoRun: true` with
`fullAutoRun: false`, under which the agent decides when a tool runs without
asking. The observation is the fresh-install default of this version, not a
setting anyone changed.

That has one consequence worth stating. With no approval gate, nothing in the
client stops the agent calling `prodpilot_deploy` by itself; the only guard is
the tool's description, which tells the agent to confirm with the developer
first and to commit its changes before deploying. A server cannot require a
client to ask. A developer who wants the gate can turn off auto-run in
Cursor's settings.

Cursor also labels every line ProdPilot writes to stderr as an error. Those
lines are ProdPilot's ordinary log, kept off stdout because stdout carries the
protocol; nothing failed.

### Windsurf, now Devin 3.10.23

Windsurf is now distributed as Devin, published by Codeium. The installed
application is `Devin (User)` 3.10.23; it runs Windsurf's language server, keeps
Windsurf's settings folder `~/.codeium/windsurf`, and its documentation for MCP
moved from the Windsurf site to Devin's. Its agent is the Devin agent, version
3000.10.23, bundled in the application.

Configuration. Two methods are documented. The Windsurf page names one global
file, `~/.codeium/windsurf/mcp_config.json`, under `mcpServers`, with
`${env:NAME}` and `${file:path}` substitution and no `${workspaceFolder}`. The
Devin agent's page names three files in order of precedence,
`.devin/mcp_config.local.json`, `.devin/mcp_config.json`, and
`%APPDATA%\devin\mcp_config.json`, and says nothing about variables. The
agent also imports other clients' MCP configs, which its logs show it doing
each time it lists servers.

Discovery failed first, and the failure is recorded as found. With the
Windsurf file in place, pointing at the absolute path of the launcher, the
panel showed a single `prodpilot` entry in error, "Connection failed, cannot
find binary path". The agent's log shows why:

```text
[MCP] environment variable 'workspaceFolder' is not set; substituting empty string
Starting stdio MCP server 'prodpilot': "/.venv/Scripts/prodpilot.exe" ["serve"]
MCP server 'prodpilot' connection failed: cannot find binary path
```

The agent had imported a committed workspace config, `.vscode/mcp.json` or
`.cursor/mcp.json`, which both use `${workspaceFolder}`, treated that variable
as an unset environment variable, and blanked it. The imported entry shares the
name `prodpilot`, and it was the one the agent tried; it never launched from the
Windsurf file. So on this version, the committed configs do not work in Devin.

Discovery passed through the agent's own highest precedence file,
`.devin/mcp_config.local.json`, naming the absolute launch command. That file
holds a path only valid on one machine, so it is listed in `.gitignore` and
never committed. After reconnecting, the log shows
`Starting stdio MCP server 'prodpilot': "E:/ProdPilot/ProdPilot/.venv/Scripts/prodpilot.exe"`
and `connected successfully`, and the panel listed all five tools. It lists them
under "Write", because ProdPilot marks none of its tools as read only.

| Check | Asked | Observed |
| --- | --- | --- |
| Content form | `node_express_insecure`, SEC-002 | STATIC, content, `insert_after` in `src/server.js`, helmet `^8.1.0`; called 11:10:06 |
| Content form, repeated | `node_express_insecure`, BLD-001 | DYNAMIC-PARAMETRIC, content, `create_file` for `Dockerfile`; called 11:11:28 |
| Constraint form | `react_vite_ready`, STR-003 | DYNAMIC-DELEGATED, constraint, `author_within_constraint` with requirement, boundary and forbidden list, no content; called 11:12:26 |
| Response size | `prodpilot_detect_stack`, `node_express_insecure` | The agent listed all 28 items by name, 5 files, 6 configs and 17 code patterns, ending with the last entry of the last list; no truncation in its logs; called 11:13:30 |

No file under `tests/samples` changed.

Per-call approval. Every one of the four ProdPilot calls stopped for approval.
The agent's log records the decision each time as
`prompting user (decision=None, perm_level=AcceptEdits)`, then
`User approved tool permission ... (grant=ApproveOnce)`. An approval covered
only the call it was given for: the same tool asked again on the next call.
This matches Devin's documentation, "MCP tools default to prompting for
approval". The agent's own steps, listing servers and tools, were allowed
without asking.

## What module 7.2 shows

Protocol compatibility held in both IDEs: once each could start the server,
every tool was listed and both contract forms came back intact, including the
largest response. What differs between clients is everything around the
protocol, and it differs in ways that matter:

- Launch configuration. VS Code and Cursor resolve `${workspaceFolder}`; Devin
  does not, and it imports the other clients' files anyway, so a config that
  works in two IDEs breaks the third. Devin needs its own local file.
- Approval. Devin asks before every call. Cursor, on its fresh-install
  default, asks before none. For the Phase 3 loop, Devin's default means a
  person approves each step, which is stricter than the fallback Section 12 of
  the Complete Solution Document plans for, a semi-automatic loop with a single
  upfront consent. Cursor's means the loop runs unattended and nothing in the
  client asks before a deploy.
- Response size. ProdPilot's largest result, 11,988 bytes, arrived whole in
  both.

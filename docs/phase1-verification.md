# Phase 1 verification record

Phase 1 exit criteria, from Section 2 of the Phased Implementation Plan:
ProdPilot registers as an MCP server in VS Code, correctly identifies a sample
project's stack, and loads the right blueprint.

Verified 21 August 2026 against commit `408bd25`, Python 3.11.15,
VS Code 1.134.0, Copilot Chat 0.62.0.

## Result

Phase 1 is closed with one stated gap. The server, detection and blueprint
criteria are met and evidenced. Invocation from Copilot agent mode specifically
could not be tested, for a reason outside the project.

| Criterion | Status | Evidence |
| --- | --- | --- |
| Registers as an MCP server | Met | Handshake and tool discovery over stdio using the exact command in `.vscode/mcp.json` |
| Discovered by VS Code | Met | VS Code read `.vscode/mcp.json` and assigned the server id `mcp.config.ws0.prodpilot` |
| Invoked from Copilot agent mode | Not tested | Blocked by account quota, see below |
| Identifies a sample project's stack | Met | Six fixtures, all correct |
| Loads the right blueprint | Met | Each supported stack loads its own blueprint, item ids disjoint |

## How the server criteria were verified

The server was launched exactly as an MCP client launches it, through
`.venv/Scripts/prodpilot.exe serve`, and driven by a real client session rather
than by calling functions in process.

- Handshake returned `prodpilot v0.1.0` on protocol `2025-11-25`.
- Tool discovery returned `prodpilot_ping` and `prodpilot_detect_stack`.
- `prodpilot_ping` returned its fixed payload with `isError` false.

## Detection and blueprint results

| Fixture | Detected | Blueprint |
| --- | --- | --- |
| `node_express_api` | `node_express` | Node.js with Express |
| `react_vite_app` | `react_vite` | React with Vite |
| `react_vite_ts_config` | `react_vite` | React with Vite |
| `vite_without_react` | `unrecognized` | none |
| `ambiguous_fullstack` | `unrecognized` | none |
| `unrecognized_python_service` | `unrecognized` | none |

Config and secrets handling was exercised alongside both, confirming no
conflict: doctor exits non-zero before setup and zero after, no credential
value appears in any output, the credential file is restricted to the current
user, it is written outside every project tree, and the MCP server continues to
answer tool calls with module 1.3 present.

Full automated suite: 62 tests, all passing.

## Open gap: Copilot agent mode invocation

Not a defect in ProdPilot and not a configuration problem. The GitHub account is
authenticated and Copilot Chat is present, but the plan's monthly allowance is
spent, so no agent request can complete. The Copilot Chat log records:

```
Server error: 402 {"error":{"message":"You have exceeded your monthly quota",
"code":"quota_exceeded"}}
copilot token sku: free_educational_quota
```

What is proven is that the server satisfies the protocol and that VS Code
discovers it from the committed workspace config. What is unproven is that
Copilot's agent invokes it, which cannot be separated from the client.

## Section 8 week-one items, carried forward

Both are properties of the VS Code client, so nothing measured outside VS Code
can answer them. They move to Phase 7, IDE Compatibility and Integration.

| Question | Status | Why it matters |
| --- | --- | --- |
| Does VS Code require per-call approval before each tool call | Open | Decides whether the Phase 3 bounded loop can run unattended or needs the semi-automatic fallback with single upfront consent |
| Is there a visible limit on tool response size | Open | Constrains how much audit output Phase 2 can return in one call |

To close both: with quota available, open this repository as the workspace
folder, start the server from MCP: List Servers, then invoke
`prodpilot_detect_stack` from agent mode twice, once against
`tests/samples/react_vite_app` (22 blueprint items) and once against
`tests/samples/node_express_api` (28 items, the largest payload available).
Record whether a confirmation appears, whether the second call re-prompts, and
whether the larger response is shown whole, truncated or summarised.

## Compatibility matrix

| Client | Discovery | Tool listing | Invocation |
| --- | --- | --- | --- |
| Direct stdio client | Confirmed | Confirmed | Confirmed |
| VS Code, Copilot agent mode | Confirmed | Not tested | Not tested |
| Cursor | Not tested | Not tested | Not tested |
| Windsurf | Not tested | Not tested | Not tested |

Cursor and Windsurf are Phase 7 work and were never in Phase 1 scope.

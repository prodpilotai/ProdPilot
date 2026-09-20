# What 1.x keeps stable

ProdPilot follows semantic versioning. This page says what that covers, so you
can depend on it, and what it does not, so a change there is not a surprise.

## Promised for every 1.x release

**The five MCP tools.** Their names, the arguments they take, and the fields
their results carry:

| Tool | Stable part |
| --- | --- |
| `prodpilot_ping` | Name, and that it answers without touching the project |
| `prodpilot_detect_stack` | `project_path`; the returned stack name and blueprint items |
| `prodpilot_fix_instruction` | `project_path` and `rule_id`; the contract's `action`, `file_path` and, for a delegated fix, `requirement`, `boundary` and `forbidden` |
| `prodpilot_fix_applied` | `project_path`, `rule_id`, `applied`, `summary`, `attempt`; the outcomes `resolved`, `unresolved` and `blocked`, and the `verified` and `regressed` fields |
| `prodpilot_deploy` | `project_path`; the nine stages, in order, each reporting pass or failure with a reason |

**Rule identifiers.** A rule keeps its identifier and its meaning. `SEC-002`
will always be the security headers rule. A rule may get better at finding a
violation, but it will not be renumbered or quietly repurposed.

**The commands and their flags.** `prodpilot serve`, `setup`, `doctor` and
`connect`, and the flags they take today.

**Where your credentials live.** `~/.prodpilot/config.toml`, readable only by
your account, and never inside a project.

**The scoring gate's three conditions.** A score of at least 90, no critical
rule failing, and a model estimate at or above its operating point.

**Python support.** 3.11 to 3.14. A later 1.x may add a newer Python; it will
not drop one.

## Not promised

- **The score a given project receives.** Rules improve, so a project may
  score differently between releases. The gate's three conditions stay the
  same; what a rule detects can get sharper.
- **The number of rules.** Version 1.0 has 50. A minor release may add more.
- **The deployability model.** It may be retrained, which changes its estimates
  and its operating point.
- **The exact wording of messages,** including refusal reasons. Read the
  outcome fields, not the prose.
- **Anything inside `prodpilot.*` imported as a library.** ProdPilot is a tool
  used over MCP and from the command line, not a library with a public API.
  Import it in your own code at your own risk.

## What a breaking change would mean

Renaming a tool, removing a rule identifier, changing what a tool's field
means, moving the config file, or dropping a supported Python would all be
version 2.0. Adding a stack, a rule, a tool or a command is a minor release.
A fix that only changes behaviour a rule already promised is a patch.

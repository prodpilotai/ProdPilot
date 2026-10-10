# Previewing fixes

```
prodpilot fix --dry-run [PROJECT] [--rule RULE_ID]
```

Shows every change the fix loop would make, in the order the loop would reach
it, and writes nothing to the project.

| Fix type | What the preview shows |
| --- | --- |
| STATIC | a unified diff per file; a new file is shown against `/dev/null` |
| DYNAMIC-PARAMETRIC | the same, or, when a value is ambiguous, the refusal and its candidates |
| DYNAMIC-DELEGATED | the constraint contract: the requirement, the files that may change, what must stay unchanged and the checker that decides; there is no diff, because your IDE agent writes the change |

The order is the loop's: priority first, then rule id. A change that edits a file
an earlier change creates, such as SEC-001 adding a non-root user to the
Dockerfile BLD-001 creates, is shown after that rule with
`depends on BLD-001, which creates Dockerfile`.

The preview applies each change to a copy of the project in the system temp
folder and removes the copy afterwards. A credential is never shown: a diff that
moves one out of source shows only its first four characters and its length.

The `prodpilot_fix_instruction` tool takes an optional `dry_run` input. With
`dry_run: true` it returns the same preview for that one rule beside the usual
contract, so the agent can show the change before applying it.

Exit codes: 0 when the preview was produced, 2 when it could not be, such as for
a path that is not a project or a rule that is not failing. `prodpilot fix`
without `--dry-run` exits 2: fixes are applied by your IDE's agent through
`prodpilot_fix_instruction`.

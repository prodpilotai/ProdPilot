# Declarations

Draft text for the Declarations section of the revised manuscript. Items in
square brackets are facts only the authors can supply.

## Funding

[State the funding source and grant number, or: The authors received no
specific funding for this work.]

## Competing interests

[State any competing interests for each author, or: The authors have no
competing interests to declare that are relevant to the content of this
article.]

## Ethics approval and consent to participate

Not applicable: the study involves no human participants and no personal data.
The dataset consists of public GitHub repositories kept only when their licence
permits reuse (MIT 458, Apache-2.0 28, ISC 5, BSD-2-Clause 4, the Unlicense 3,
BSD-3-Clause 2). Each project was deployed to the Render free plan through the
provider's documented API, with service creation paced below the platform's
limit of 20 per hour. A service was publicly reachable only while it was being
labelled and was deleted immediately afterwards; a final query confirmed that
no service was left running. The audit records whether a credential-shaped
string is present, but detected values were never tested against a provider,
never stored in the dataset, which keeps only rule outcomes and counts, and
never transmitted. The synthetic negatives that break a secret-scanning rule
use a fabricated credential-shaped string that matches no provider account.

## Data availability

The dataset manifest (repository, commit, licence, stack, label, build result
and split for every labelled project), the feature matrix, the frozen ruleset
and the analysis outputs are deposited at [Zenodo DOI]. The repositories
themselves are not redistributed; each can be retrieved at its recorded commit.

## Code availability

ProdPilot is available at https://github.com/prodpilotai/ProdPilot under the MIT
licence and as the `prodpilot` package on PyPI, version 1.0.0. The revised
experiments were run at the commit tagged [tag], and every number in the paper
is produced by a script listed in the bundle's README.

## Author contributions

[Assign CRediT roles, for example: M.S.K.: Conceptualization, Methodology,
Software, Investigation, Data curation, Writing - original draft. M.J.A.:
Supervision, Methodology, Writing - review and editing. R.N.A.: [roles].
R.F.A.: Supervision, Writing - review and editing.]

## Use of AI tools

[The authors must state this accurately under Springer Nature policy. Two uses
occurred and both belong here. First, as research subjects: the delegated-fix
trials used Grok 4.6 through Cursor and Claude Opus 5 through Claude Code, as
reported in the evaluation. Second, as assistance: state whether an AI
assistant was used in writing the analysis code or drafting or editing the
manuscript, and to what extent, and that the authors reviewed all content and
take responsibility for it.]

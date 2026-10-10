# Node.js versions

ProdPilot reads the Node version a project declares, uses it for the Dockerfile
and CI workflow it generates, and warns when the version has reached end of life.

## Where the version is read from

The same places Render reads, in the same order, highest first
([render.com/docs/node-version](https://render.com/docs/node-version)):

1. `NODE_VERSION` in the project's `.env`. ProdPush sends `.env` values to
   Render, where `NODE_VERSION` decides the version.
2. `.node-version`
3. `.nvmrc`
4. `engines.node` in `package.json`

A `NODE_VERSION` set by hand in the Render dashboard is not visible from the
project and is not read. The detection tool, `prodpilot_detect_stack`, returns
what it found under `blueprint.node`, with any notice under `blueprint.notices`.

## Which version a generated file uses

| What the project declares | The Dockerfile and workflow use |
| --- | --- |
| One major on an LTS line: 20, 22, 24 or 26, such as `22`, `^24`, `>=20 <21`, `lts/jod` | that major |
| Nothing | Node 24, Render's default for new services and the Active LTS line |
| One major that is not a supported LTS line, such as `18` or `25` | Node 24, with a notice |
| A range spanning more than one major, or with no upper bound, such as `>=18` | nothing: the contract refuses and lists the supported majors the range allows |

An unbounded range is refused rather than resolved because Render resolves it to
the newest release, which changes over time.

The LTS lines and their end-of-life dates come from the Node.js release schedule:
Node 20 reached end of life on 30 April 2026, Node 22 ends on 30 April 2027,
Node 24 on 30 April 2028, and Node 26 becomes LTS on 28 October 2026.

## The end-of-life notice

A project that declares an end-of-life major, or declares different majors in
different places, gets an informational notice in `prodpilot audit`, in every
report format, and from the detection tool. A notice is not a rule. It does not
change the score and does not block the gate.

Rule BLD-005 belongs to the frozen 1.x ruleset and still asks `package.json` to
pin Node 20. The notice says so when it applies. That rule changes in 2.0, with a
new model and a new evaluation, because changing it in 1.x would change scores
and the model's inputs.

## What did not change

The deployability model's build feature runs Render's build command in a local
Node image, choosing the version from `.node-version`, `.nvmrc` and then
`engines.node`, with an unbounded range resolved to the newest local image and
anything unresolvable to Node 24. That computation is an input to the model and
is exactly as 1.0.0 shipped it.

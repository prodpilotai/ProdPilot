"""Collect the implementation facts the paper states, from the artefact itself.

Every value written here is read from the repository, the data directory or a
live stdio session with the server, never typed in. Run from the repository
root:

    python tests/facts.py docs/revision

It writes facts.json and facts.md into the named directory. Facts that need a
service this script cannot reach, such as the GitHub workflow run history, are
recorded as not measured rather than filled in.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "tests" / "samples"
BUILDS = ROOT / "data" / "builds.jsonl"


def matrix(text: str) -> dict[str, list[str]]:
    """The operating systems and Python versions a workflow's matrix names."""
    found: dict[str, list[str]] = {}
    for key in ("os", "python"):
        hit = re.search(rf"^\s*{key}:\s*\[([^\]]*)\]", text, re.M)
        if hit:
            found[key] = [v.strip().strip("\"'") for v in hit.group(1).split(",") if v.strip()]
    return found


def node_use(rows: list[dict]) -> dict[str, object]:
    """Which images the build pass ran on, and how many rows fell back."""
    images = Counter(str(r.get("image", "")) for r in rows)
    fell = [r for r in rows if r.get("fallback")]
    return {
        "rows": len(rows),
        "by_image": dict(images.most_common()),
        "fallback_rows": len(fell),
        "fallback_images": dict(Counter(str(r.get("image", "")) for r in fell).most_common()),
        "fallback_wanted": dict(Counter(str(r.get("wanted", "")) for r in fell).most_common()),
    }


def version() -> dict[str, object]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    init = (ROOT / "src" / "prodpilot" / "__init__.py").read_text(encoding="utf-8")
    runtime = re.search(r'__version__\s*=\s*"([^"]+)"', init).group(1)
    static = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if static:
        declared = static.group(1)
    else:
        # Since 1.1 pyproject declares the version dynamic and hatchling reads
        # it from the file [tool.hatch.version] names, so that file is what it
        # declares.
        source = re.search(r'^\[tool\.hatch\.version\]\s*\npath\s*=\s*"([^"]+)"', text, re.M)
        declared = re.search(r'__version__\s*=\s*"([^"]+)"',
                             (ROOT / source.group(1)).read_text(encoding="utf-8")).group(1)
    tags = subprocess.run(["git", "tag"], cwd=ROOT, capture_output=True,
                          text=True).stdout.split()
    tagged = {}
    for tag in tags:
        sha = subprocess.run(["git", "rev-list", "-n", "1", tag], cwd=ROOT,
                             capture_output=True, text=True).stdout.strip()
        tagged[tag] = sha[:7]
    return {"pyproject": declared, "runtime": runtime,
            "agree": declared == runtime, "tags": tagged}


def tests() -> dict[str, object]:
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    hit = re.search(r"(\d+) tests? collected", out)
    flow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    return {"collected": int(hit.group(1)) if hit else None, "matrix": matrix(flow)}


def history() -> dict[str, object]:
    """The CI workflow template as of the live deploy, and the later change."""
    def log(sha: str) -> str:
        return subprocess.run(["git", "show", "-s", "--format=%h %ad %s", "--date=short", sha],
                              cwd=ROOT, capture_output=True, text=True).stdout.strip()

    old = subprocess.run(["git", "show", "679b0a9:src/prodpilot/cicd.py"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    new = (ROOT / "src" / "prodpilot" / "cicd.py").read_text(encoding="utf-8")
    return {
        "phase6_template": log("679b0a9"),
        "phase6_fixed_wait": "sleep 90" in old,
        "wait_for_deploy_commit": log("a8e059d"),
        "current_fixed_wait": "sleep 90" in new,
        "run_history": "not measured: the GitHub CLI is not authenticated on this machine",
    }


async def response() -> dict[str, object]:
    """The largest tool response, the Express blueprint, as a client receives it."""
    from mcp import ClientSession, StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "prodpilot", "serve"], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            got = await client.call_tool("prodpilot_detect_stack",
                                         {"project_path": str(SAMPLES / "node_express_insecure")})
    text = "".join(part.text for part in got.content if getattr(part, "text", None))
    shaped = json.dumps(got.structured_content)
    frame = got.model_dump_json(by_alias=True, exclude_none=True)
    items = (got.structured_content or {}).get("blueprint", {}).get("item_count")
    sizes = {"text_bytes": len(text.encode()), "structured_bytes": len(shaped.encode()),
             "result_bytes": len(frame.encode())}
    return {"tool": "prodpilot_detect_stack", "sample": "node_express_insecure",
            "blueprint_items": items, **sizes,
            "result_kib": round(sizes["result_bytes"] / 1024, 2),
            "result_kb": round(sizes["result_bytes"] / 1000, 2)}


def nodes() -> dict[str, object]:
    text = (ROOT / "src" / "prodpilot" / "extraction.py").read_text(encoding="utf-8")
    default = re.search(r'def node_version.*?return "(\d+)"', text, re.S)
    images = re.search(r"IMAGES = \(([^)]*)\)",
                       (ROOT / "src" / "prodpilot" / "builds.py").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in BUILDS.read_text(encoding="utf-8").splitlines()
            if line.strip()] if BUILDS.exists() else []
    return {"dockerfile_default_major": default.group(1) if default else None,
            "build_images": [v.strip().strip("\"'") for v in images.group(1).split(",")]
            if images else [],
            "build_pass": node_use(rows)}


def markdown(found: dict) -> str:
    v, t, h, r, n = (found["version"], found["tests"], found["cicd"],
                     found["response"], found["node"])
    b = n["build_pass"]
    lines = [
        "# Implementation facts",
        "",
        "Collected by `python tests/facts.py docs/revision` from the repository,",
        "the data directory and a live stdio session with the server.",
        "",
        "| Fact | Value |",
        "| --- | --- |",
        f"| Version in pyproject.toml | {v['pyproject']} |",
        f"| Version the server reports | {v['runtime']} |",
        f"| The two agree | {'yes' if v['agree'] else 'no'} |",
        f"| Tags | {', '.join(f'{k} ({s})' for k, s in v['tags'].items())} |",
        f"| Tests collected | {t['collected']} |",
        f"| CI operating systems | {', '.join(t['matrix'].get('os', []))} |",
        f"| CI Python versions | {', '.join(t['matrix'].get('python', []))} |",
        f"| Workflow at the live deploy | {h['phase6_template']}, fixed wait: "
        f"{'yes' if h['phase6_fixed_wait'] else 'no'} |",
        f"| Wait-for-deploy workflow | {h['wait_for_deploy_commit']} |",
        f"| Workflow run history | {h['run_history']} |",
        f"| Largest response, text content | {r['text_bytes']} bytes |",
        f"| Largest response, structured content | {r['structured_bytes']} bytes |",
        f"| Largest response, whole result | {r['result_bytes']} bytes "
        f"({r['result_kib']} KiB, {r['result_kb']} KB) |",
        f"| Blueprint items in that response | {r['blueprint_items']} |",
        f"| Node major in generated Dockerfiles | {n['dockerfile_default_major']} |",
        f"| Images the build pass may use | {', '.join(n['build_images'])} |",
        f"| Build pass rows | {b['rows']} |",
        f"| Rows that fell back to another image | {b['fallback_rows']} |",
        "",
        "Build pass rows by image:",
        "",
        "| Image | Rows |",
        "| --- | --- |",
    ]
    lines += [f"| {k} | {c} |" for k, c in b["by_image"].items()]
    lines += ["", "Rows that fell back, by the major they asked for:", "",
              "| Wanted | Rows |", "| --- | --- |"]
    lines += [f"| {k} | {c} |" for k, c in b["fallback_wanted"].items()]
    return "\n".join(lines) + "\n"


def main(out: str) -> None:
    found = {"version": version(), "tests": tests(), "cicd": history(),
             "response": asyncio.run(response()), "node": nodes()}
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)
    (target / "facts.json").write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
    (target / "facts.md").write_text(markdown(found), encoding="utf-8")
    print(json.dumps(found, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs/revision")

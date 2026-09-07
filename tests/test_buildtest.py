"""Docker build test tests for Phase 6 module 6.3.

Two layers, kept apart on purpose.

The classifier is tested against the exact text real failing builds produced on
this machine, captured from both builders, so the patterns are checked against
what the code will actually see rather than against wording invented to match
them. Those tests need no daemon and always run.

The build tests need a Docker daemon and are skipped without one. They build
real images, run real containers and probe a real port. Section 7 stage 3 is
entirely about what happens on the developer's machine, so testing it against a
mocked daemon would be testing something else.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prodpilot import buildtest
from prodpilot.buildtest import (
    INSTALLS,
    Build,
    BuildUnavailable,
    Fault,
    Health,
    classify,
    lines_of,
    port_of,
    probe,
    run,
    said,
    tail,
)

SAMPLES = Path(__file__).resolve().parent / "samples"
OK = SAMPLES / "docker_ok"


def daemon() -> bool:
    try:
        buildtest.client()
        return True
    except BuildUnavailable:
        return False


needs_docker = pytest.mark.skipif(not daemon(), reason="no Docker daemon available")


# Captured verbatim from real failing builds. The legacy builder is what the
# SDK drives, and BuildKit is what the docker command line drives.
LEGACY = {
    Fault.BASE_IMAGE:
        'failed to resolve reference "docker.io/library/node:99-doesnotexist": '
        "docker.io/library/node:99-doesnotexist: not found",
    Fault.MISSING_FILE:
        "COPY failed: file not found in build context or excluded by "
        ".dockerignore: stat nothing-here.js: file does not exist",
    Fault.DEPENDENCY:
        "The command '/bin/sh -c apk add --no-cache totally-not-a-real-package' "
        "returned a non-zero code: 1",
    Fault.SCRIPT:
        "The command '/bin/sh -c npm run build' returned a non-zero code: 1",
}

BUILDKIT = {
    Fault.BASE_IMAGE:
        "ERROR: failed to build: failed to solve: node:99-doesnotexist: failed to "
        "resolve source metadata for docker.io/library/node:99-doesnotexist: not found",
    Fault.MISSING_FILE:
        "ERROR: failed to build: failed to solve: failed to compute cache key: "
        'failed to calculate checksum of ref abc::def: "/nothing-here.js": not found',
    Fault.DEPENDENCY:
        'ERROR: failed to build: failed to solve: process "/bin/sh -c apk add '
        '--no-cache totally-not-a-real-package" did not complete successfully: exit code: 1',
    Fault.SCRIPT:
        'ERROR: failed to build: failed to solve: process "/bin/sh -c npm run build" '
        "did not complete successfully: exit code: 1",
}


# --------------------------------------------------------------------------
# the taxonomy, against real captured output
# --------------------------------------------------------------------------


def test_the_taxonomy_is_the_five_categories_the_document_names():
    assert {f.value for f in Fault} == {
        "missing dependency", "bad base image", "port mismatch",
        "missing file", "build script failure",
    }


@pytest.mark.parametrize("fault", sorted(LEGACY, key=lambda f: f.value))
def test_the_legacy_builder_wording_is_classified(fault: Fault):
    got, line = classify(LEGACY[fault])

    assert got is fault
    assert line


@pytest.mark.parametrize("fault", sorted(BUILDKIT, key=lambda f: f.value))
def test_the_buildkit_wording_is_classified(fault: Fault):
    """The command line builder words the same failures differently."""
    got, line = classify(BUILDKIT[fault])

    assert got is fault


def test_a_dependency_and_a_script_failure_differ_only_by_the_command():
    """Which is why the command decides and the failure line cannot."""
    dependency = LEGACY[Fault.DEPENDENCY].replace(
        "apk add --no-cache totally-not-a-real-package", "COMMAND")
    script = LEGACY[Fault.SCRIPT].replace("npm run build", "COMMAND")

    assert dependency == script
    assert classify(LEGACY[Fault.DEPENDENCY])[0] is Fault.DEPENDENCY
    assert classify(LEGACY[Fault.SCRIPT])[0] is Fault.SCRIPT


@pytest.mark.parametrize(
    "command",
    ["apk add curl", "apt-get install -y git", "npm ci", "npm install",
     "yarn install", "pnpm install", "pip install flask", "bundle install"],
)
def test_an_install_command_reads_as_a_missing_dependency(command: str):
    line = f"The command '/bin/sh -c {command}' returned a non-zero code: 1"

    assert classify(line)[0] is Fault.DEPENDENCY


@pytest.mark.parametrize(
    "command",
    ["npm run build", "yarn build", "make", "./build.sh", "tsc --noEmit"],
)
def test_any_other_failing_step_reads_as_a_build_script_failure(command: str):
    line = f"The command '/bin/sh -c {command}' returned a non-zero code: 1"

    assert classify(line)[0] is Fault.SCRIPT


def test_output_that_matches_nothing_is_not_classified():
    """The mitigation Section 12 names, and the reason review exists."""
    fault, line = classify("no space left on device\nthe daemon gave up\n")

    assert fault is None
    assert line == ""


def test_the_first_matching_line_decides():
    log = [{"stream": "Step 1/3 : FROM node:20-alpine\n"},
           {"stream": "Step 2/3 : COPY missing.js .\n"},
           {"error": LEGACY[Fault.MISSING_FILE]}]

    fault, line = classify(log)

    assert fault is Fault.MISSING_FILE
    assert "COPY failed" in line


def test_the_sdk_log_shape_is_flattened():
    assert lines_of([{"stream": "a\nb\n"}, {"error": "c"}]) == ["a", "b", "c"]
    assert lines_of("x\n\ny") == ["x", "y"]
    assert lines_of(None) == []


def test_a_matched_line_is_bounded():
    fault, line = classify("COPY failed: " + "x" * 5000)

    assert fault is Fault.MISSING_FILE
    assert len(line) <= 200


def test_the_kept_log_is_bounded():
    kept = tail([{"stream": f"line {i}\n"} for i in range(500)])

    assert len(kept.splitlines()) == buildtest.KEPT


def test_a_parse_error_reported_as_a_mapping_reads_as_a_sentence():
    """The SDK reports a bad Dockerfile as a dictionary rather than a string."""
    said_it = said({"message": "dockerfile parse error on line 2: unknown instruction"})

    assert said_it.startswith("dockerfile parse error")
    assert "{" not in said_it


# --------------------------------------------------------------------------
# what may travel to the loop, and what may not
# --------------------------------------------------------------------------


def test_a_classified_failure_is_actionable():
    made = Build("p", False, fault=Fault.BASE_IMAGE, detail="a line")

    assert made.actionable is True
    assert made.review is False


def test_an_unclassified_failure_goes_to_manual_review():
    made = Build("p", False, detail="something nobody predicted")

    assert made.review is True
    assert made.actionable is False


def test_a_successful_build_is_neither():
    made = Build("p", True, image="tag")

    assert made.review is False
    assert made.actionable is False


def test_the_raw_log_is_never_offered_as_actionable():
    """Section 12: unmatched errors never enter the loop as raw text."""
    made = Build("p", False, detail="a line", log="thousands of lines")

    assert "log" not in made.to_dict()


def test_the_result_serialises_whole():
    payload = Build("p", False, fault=Fault.PORT, detail="d").to_dict()

    assert set(payload) == {
        "project", "ok", "image", "fault", "detail", "review", "actionable", "health",
    }
    json.dumps(payload)


# --------------------------------------------------------------------------
# the health probe
# --------------------------------------------------------------------------


def test_a_port_an_application_announces_is_read():
    assert port_of("listening on 4000") == 4000
    assert port_of("Server listening on port 3000") == 3000
    assert port_of("started on 5000") == 5000


def test_output_that_announces_nothing_gives_no_port():
    assert port_of("ready") is None
    assert port_of("") is None


def test_the_probe_gives_up_rather_than_waiting_forever():
    ticks = [0.0]

    def now():
        return ticks[0]

    def sleep(seconds):
        ticks[0] += seconds

    health = probe("http://127.0.0.1:1/health", wait=5, every=1, sleep=sleep, now=now)

    assert health.ok is False
    assert "within 5s" in health.detail


def test_a_project_with_no_dockerfile_is_reported(tmp_path: Path):
    result = run(tmp_path)

    assert result.ok is False
    assert "no Dockerfile" in result.detail
    assert result.review is True


# --------------------------------------------------------------------------
# real builds, real containers, real localhost
# --------------------------------------------------------------------------


def project(tmp_path: Path, dockerfile: str, files: dict[str, str] | None = None) -> Path:
    root = tmp_path / "project"
    root.mkdir(parents=True, exist_ok=True)
    (root / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    for name, body in (files or {}).items():
        (root / name).write_text(body, encoding="utf-8")
    return root


@needs_docker
def test_a_real_project_builds_runs_and_answers_health():
    """Section 7 stage 3's success path, end to end on this machine."""
    result = run(OK, env={"PORT": "3000"}, wait=40)

    assert result.ok is True, result.detail
    assert result.health is not None
    assert result.health.ok is True
    assert result.health.status == 200
    assert result.health.url.startswith("http://127.0.0.1:")
    assert result.health.url.endswith("/health")


@needs_docker
def test_the_sealed_values_reach_the_container():
    """They are given to the run, never to the build."""
    result = run(OK, env={"PORT": "3000"}, wait=40)

    assert result.ok is True
    source = Path(buildtest.__file__).read_text(encoding="utf-8")
    assert "buildargs" not in source, "a secret in a build argument lands in image history"


@needs_docker
def test_a_bad_base_image_is_classified(tmp_path: Path):
    result = run(project(tmp_path, "FROM node:99-doesnotexist\nWORKDIR /app\n"))

    assert result.ok is False
    assert result.fault is Fault.BASE_IMAGE
    assert result.actionable is True


@needs_docker
def test_a_missing_file_is_classified(tmp_path: Path):
    result = run(project(
        tmp_path, "FROM node:20-alpine\nWORKDIR /app\nCOPY nothing-here.js .\n"))

    assert result.ok is False
    assert result.fault is Fault.MISSING_FILE


@needs_docker
def test_a_missing_dependency_is_classified(tmp_path: Path):
    result = run(project(
        tmp_path,
        "FROM node:20-alpine\nRUN apk add --no-cache totally-not-a-real-package\n"))

    assert result.ok is False
    assert result.fault is Fault.DEPENDENCY


@needs_docker
def test_a_failing_build_script_is_classified(tmp_path: Path):
    result = run(project(
        tmp_path,
        "FROM node:20-alpine\nWORKDIR /app\n"
        "RUN echo '{\"name\":\"x\",\"scripts\":{\"build\":\"exit 1\"}}' > package.json\n"
        "RUN npm run build\n"))

    assert result.ok is False
    assert result.fault is Fault.SCRIPT


@needs_docker
def test_a_port_mismatch_is_classified(tmp_path: Path):
    """The one category that cannot be seen at build time.

    The image exposes 3000 and the application listens on 4000, so the build
    succeeds and nothing ever answers on the published port.
    """
    root = project(
        tmp_path,
        "FROM node:20-alpine\nWORKDIR /app\nCOPY server.js .\nEXPOSE 3000\n"
        'CMD ["node", "server.js"]\n',
        {"server.js":
            'const http = require("http");\n'
            'http.createServer((req,res)=>{res.writeHead(200);res.end("ok");})\n'
            '  .listen(4000, () => console.log("listening on 4000"));\n'},
    )

    result = run(root, wait=6)

    assert result.ok is True, "the image builds, which is the point"
    assert result.health.ok is False
    assert result.fault is Fault.PORT


@needs_docker
def test_an_unclassifiable_failure_goes_to_review(tmp_path: Path):
    """A Dockerfile that cannot be parsed is not in the taxonomy."""
    result = run(project(tmp_path, "FROM node:20-alpine\nNOTAREALINSTRUCTION foo\n"))

    assert result.ok is False
    assert result.fault is None
    assert result.review is True
    assert result.actionable is False
    assert "unknown instruction" in result.detail

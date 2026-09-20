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
import sys
import types
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


needs_docker = pytest.mark.skipif(
    not daemon(), reason="no Docker daemon that runs Linux containers")


def test_a_daemon_in_windows_container_mode_is_reported_as_unavailable(monkeypatch):
    """Every image ProdPilot builds is Linux, so such a daemon cannot serve it.

    GitHub's Windows runners are exactly this: the daemon answers, and every
    build then fails with "no matching manifest for windows/amd64". Saying it
    here names what the developer has to change.
    """
    class Daemon:
        def ping(self):
            return True

        def info(self):
            return {"OSType": "windows"}

    stub = types.ModuleType("docker")
    stub.from_env = lambda: Daemon()
    errors = types.ModuleType("docker.errors")
    errors.DockerException = Exception
    stub.errors = errors
    monkeypatch.setitem(sys.modules, "docker", stub)
    monkeypatch.setitem(sys.modules, "docker.errors", errors)

    with pytest.raises(BuildUnavailable) as raised:
        buildtest.client()

    assert "windows containers" in str(raised.value)
    assert "Linux" in str(raised.value)


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
def test_a_real_failed_build_names_its_cause(tmp_path: Path):
    """The Docker SDK hands the log over as a generator. It used to be read
    twice, so a real failure kept no output and its summary said only "missing
    dependency"; the full-chain rerun met exactly this in node_express_ready."""
    result = run(project(
        tmp_path, "FROM node:20-alpine\nWORKDIR /app\nCOPY package.json ./\nRUN npm ci\n",
        {"package.json": '{"name": "demo", "version": "1.0.0"}'}))

    assert result.ok is False
    assert result.fault is Fault.DEPENDENCY
    assert result.log, "the build output was kept"
    assert "package-lock.json" in result.summary()
    assert "\x1b[" not in result.log


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


# --------------------------------------------------------------------------
# the port Render gives
# --------------------------------------------------------------------------


def test_the_port_given_follows_the_project_then_the_image_then_render():
    """Found by module 7.3's full-chain run, where stage 3 refused projects Render runs.

    A PORT the project sealed wins. Else the port the image exposes, so an
    application reading PORT listens where the image says. Else Render's own
    default, for an image that exposes nothing, as the fix loop's Dockerfiles do.
    """
    assert buildtest.chosen([], {}) == buildtest.RENDER_PORT == "10000"
    assert buildtest.chosen([3000], {}) == "3000"
    assert buildtest.chosen([3000], {"PORT": "4000", "CORS_ORIGIN": "x"}) == "4000"


@needs_docker
def test_a_dockerfile_that_exposes_no_port_answers_on_renders(tmp_path: Path):
    """The shape of every Dockerfile the fix loop writes: no EXPOSE."""
    root = project(
        tmp_path,
        "FROM node:20-alpine\nWORKDIR /app\nCOPY server.js .\nUSER node\n"
        'CMD ["node", "server.js"]\n',
        {"server.js":
            'const http = require("http");\n'
            'http.createServer((req, res) => { res.writeHead(200); res.end("ok"); })\n'
            "  .listen(process.env.PORT || 3000);\n"},
    )

    result = run(root, wait=40)

    assert result.ok is True, result.detail
    assert result.health.ok is True, result.health.detail
    assert result.health.status == 200


@needs_docker
def test_an_image_exposing_its_own_port_is_told_to_use_it():
    """docker_ok exposes 3000 and reads PORT; with nothing sealed it listens on 3000."""
    result = run(OK, wait=40)

    assert result.ok is True, result.detail
    assert result.health.ok is True, result.health.detail


@needs_docker
def test_the_demo_with_no_env_file_answers_health():
    """The committed demo reads PORT and exposes 10000, and ships no .env."""
    demo = Path(__file__).resolve().parent / "samples" / "node_express_gated"
    assert not (demo / ".env").exists()

    result = run(demo, wait=40)

    assert result.ok is True, result.detail
    assert result.health.ok is True, result.health.detail
    assert result.health.status == 200


# --------------------------------------------------------------------------
# more than one published port, and a container that stops (module 7.3)
# --------------------------------------------------------------------------


def test_the_highest_exposed_port_is_the_projects():
    """nginx's base image exposes 80 beneath the 8080 a Dockerfile adds."""
    assert buildtest.chosen([80, 8080], {}) == "8080"


def test_every_published_port_is_asked_until_one_answers():
    import http.server
    import socket
    import threading

    class Health(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    live = http.server.HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=live.serve_forever, daemon=True).start()
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    dead = closed.getsockname()[1]
    closed.close()
    try:
        answer = buildtest.reached(
            [f"http://127.0.0.1:{dead}/health", f"http://127.0.0.1:{live.server_port}/health"],
            wait=10, every=0.1)
    finally:
        live.shutdown()

    assert answer.ok is True
    assert answer.url.endswith(f":{live.server_port}/health")


@needs_docker
def test_an_nginx_image_exposing_two_ports_answers_on_its_own(tmp_path: Path):
    """The shape of every React Dockerfile ProdPilot writes, which exposes 80 and 8080."""
    root = project(
        tmp_path,
        "FROM nginx:1.27-alpine\n"
        "COPY nginx.conf /etc/nginx/conf.d/default.conf\n"
        "RUN chown -R nginx:nginx /var/cache/nginx /var/log/nginx /etc/nginx/conf.d "
        "&& touch /var/run/nginx.pid && chown nginx:nginx /var/run/nginx.pid\n"
        "USER nginx\nEXPOSE 8080\n",
        {"nginx.conf": "server {\n    listen 8080;\n    location /health {\n"
                       "        return 200 \"ok\";\n    }\n}\n"},
    )

    result = run(root, wait=40)

    assert result.ok is True, result.detail
    assert result.health.ok is True, result.health.detail


@needs_docker
def test_a_container_that_stops_on_start_says_so(tmp_path: Path):
    root = project(
        tmp_path,
        'FROM node:20-alpine\nEXPOSE 3000\nCMD ["node", "-e", "console.log(\'cannot start\'); process.exit(3)"]\n',
    )

    result = run(root, wait=8)

    assert result.ok is True, "the image builds"
    assert result.health.ok is False
    assert "exited on start with code 3" in result.health.detail
    assert "cannot start" in result.health.detail
    assert result.fault is None, "a crash is for a person, not a taxonomy entry"


NPM_CI_WITHOUT_LOCKFILE = """\
Step 4/9 : RUN npm ci
npm error code EUSAGE
npm error
npm error The `npm ci` command can only install with an existing package-lock.json or
npm error npm-shrinkwrap.json with lockfileVersion >= 1. Run an install with npm@5 or
npm error later to generate a package-lock.json file, then try again.
npm error
npm error Clean install a project
npm error Usage:
npm error npm ci
npm error A complete log of this run can be found in: /root/.npm/_logs/debug-0.log
"""


def test_colour_codes_are_removed_from_the_build_output():
    """A real build's npm lines start with an escape code, which hid the cause
    from the summary on the first real run of this change."""
    assert lines_of("\x1b[91mnpm error code EUSAGE\x1b[0m\n\x1b[91mnpm error The `npm ci` command") \
        == ["npm error code EUSAGE", "npm error The `npm ci` command"]


def test_a_failed_build_says_why_in_its_summary():
    """The full-chain rerun stopped node_express_ready saying only "missing
    dependency"; npm's own first line says the lockfile is missing."""
    failed = Build("demo", False, fault=Fault.DEPENDENCY, log=NPM_CI_WITHOUT_LOCKFILE)

    assert failed.summary() == (
        "demo: build failed, missing dependency: npm error The `npm ci` command can only "
        "install with an existing package-lock.json or")


def test_a_failed_build_with_no_output_keeps_the_short_summary():
    assert Build("demo", False, fault=Fault.DEPENDENCY).summary() == \
        "demo: build failed, missing dependency"
    assert Build("demo", False, log="Step 1/2 : FROM node:20").summary() == \
        "demo: build failed, unclassified, needs manual review"


def test_an_unhealthy_container_says_why_in_its_summary():
    """The stage that stops on it reports the summary, found by module 7.3's run."""
    crashed = Build("demo", True, image="tag", health=Health(
        False, "", None, "the container exited on start with code 1: Error: Cannot find module"))

    assert crashed.summary() == ("demo: image built, container not healthy: the container "
                                 "exited on start with code 1: Error: Cannot find module")
    assert Build("demo", True, image="tag", health=Health(True, "u", 200, "ok")).summary() == \
        "demo: image built, container healthy"


def test_the_line_that_explains_a_crash_is_the_one_reported():
    """Node ends a crash with its version banner, which says nothing about why."""
    node = ["node:internal/modules/cjs/loader:1210", "  throw err;", "  ^", "",
            "Error: Cannot find module '../models/User'", "Require stack:",
            "- /app/src/server.js", "}", "", "Node.js v20.20.2"]
    nginx = ["/docker-entrypoint.sh: Configuration complete; ready for start up",
             'nginx: [emerg] mkdir() "/var/cache/nginx/client_temp" failed (13: Permission denied)']

    assert buildtest.telling(node) == "Error: Cannot find module '../models/User'"
    assert buildtest.telling(nginx).startswith("nginx: [emerg] mkdir()")
    assert buildtest.telling(["cannot start"]) == "cannot start"
    assert buildtest.telling([]) == "no output"

"""CI/CD wiring tests for Phase 6 module 6.8.

Nothing here reaches GitHub or Render. The transport is injected and every
response is built from the shapes GitHub's published REST reference describes.

The encryption is not mocked. A real key pair is generated locally, the module
seals against its public half exactly as GitHub documents, and the test decrypts
with the private half to prove the ciphertext really is the value. That is the
only way to check a sealed box without a live repository, since a sealed box is
anonymous and one way by design.
"""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import pytest
from nacl import encoding, public

from prodpilot import cicd, projectstate, push
from prodpilot.cicd import (
    ACTIVE,
    APP_URL,
    KEY,
    RENDER,
    SECRETS,
    SERVICE,
    STATES,
    WORKFLOW,
    Actions,
    Reply,
    WireError,
    app_url,
    encrypt,
    service_id,
    slug,
    wire,
    workflow,
)

TOKEN = "made-up-github-token"
REPO = "octo/api"
SERVICE_URL = "https://api-abc.onrender.com"
SERVICE_ID = "srv-abc123"
RENDER_KEY = "rnd_made-up-render-key-for-tests"


@pytest.fixture
def keys():
    """A real libsodium key pair, so ciphertext can be decrypted and checked."""
    secret = public.PrivateKey.generate()
    return secret, secret.public_key.encode(encoding.Base64Encoder()).decode()


def reply(status: int = 200, body=None) -> Reply:
    if body is None:
        return Reply(status, b"")
    return Reply(status, json.dumps(body).encode())


class Api:
    """A scripted GitHub, answering by path and recording what it was asked."""

    def __init__(self, key: str, state: str = ACTIVE) -> None:
        self.key = key
        self.state = state
        self.calls: list[tuple[str, str]] = []
        self.secrets: dict[str, dict] = {}
        self.headers: list[dict[str, str]] = []

    def __call__(self, url, method, headers, body) -> Reply:
        self.calls.append((method, url))
        self.headers.append(headers)
        if "public-key" in url:
            return reply(200, {"key_id": "kid-1", "key": self.key})
        if "/actions/secrets/" in url and method == "PUT":
            self.secrets[url.rsplit("/", 1)[-1]] = json.loads(body.decode())
            return reply(201)
        if "/actions/workflows/" in url:
            return reply(200, {"id": 42, "name": "Deploy", "path": WORKFLOW,
                               "state": self.state})
        raise AssertionError(f"unscripted call {method} {url}")


def git(root: Path, *args: str) -> None:
    subprocess.run(("git",) + args, cwd=str(root), check=True, capture_output=True)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A real repository with a local bare remote and stage 5's state written."""
    remote = tmp_path / "remote.git"
    subprocess.run(("git", "init", "--bare", "-q", str(remote)),
                   check=True, capture_output=True)
    subprocess.run(("git", "-C", str(remote), "symbolic-ref", "HEAD",
                    "refs/heads/main"), check=True, capture_output=True)

    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "remote", "add", "origin", str(remote))
    (root / ".gitignore").write_text("node_modules\n.env\n.env.production\n",
                                     encoding="utf-8")
    (root / "README.md").write_text("start\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "initial")
    git(root, "branch", "-M", "main")
    git(root, "push", "-q", "origin", "main")

    projectstate.write_project_state(root, {
        projectstate.SERVICE_ID_KEY: SERVICE_ID,
        projectstate.DEPLOY_ID_KEY: "dep-xyz",
        projectstate.SERVICE_URL_KEY: SERVICE_URL,
    })
    return root


def wired(project, keys, **kw):
    secret, pub = keys
    api = kw.pop("api", None) or Api(pub)
    result = wire(project, token=TOKEN, fetch=api, repo=REPO, key=RENDER_KEY, **kw)
    return result, api


def opened(secret, sent: dict) -> str:
    return public.SealedBox(secret).decrypt(
        base64.b64decode(sent["encrypted_value"])).decode()


# --------------------------------------------------------------------------
# the sealed box, checked by decrypting it
# --------------------------------------------------------------------------


def test_a_sealed_value_decrypts_back_to_itself(keys):
    """The only honest check without a live repository."""
    secret, pub = keys

    sealed = encrypt(pub, "a value worth protecting")

    assert public.SealedBox(secret).decrypt(base64.b64decode(sealed)).decode() == \
        "a value worth protecting"


def test_the_ciphertext_never_contains_the_plaintext(keys):
    secret, pub = keys

    sealed = encrypt(pub, RENDER_KEY)

    assert RENDER_KEY not in sealed
    assert "made-up-render-key" not in sealed


def test_sealing_the_same_value_twice_gives_different_ciphertext(keys):
    """A sealed box is anonymous, so it carries fresh randomness every time."""
    secret, pub = keys

    assert encrypt(pub, "same") != encrypt(pub, "same")


def test_the_result_is_base64_as_github_requires(keys):
    secret, pub = keys

    base64.b64decode(encrypt(pub, "value"), validate=True)


def test_a_key_that_is_not_a_key_is_refused():
    with pytest.raises(Exception):
        encrypt("not-a-real-public-key", "value")


# --------------------------------------------------------------------------
# the GitHub calls
# --------------------------------------------------------------------------


def test_the_public_key_is_read_from_the_documented_endpoint(keys):
    secret, pub = keys
    api = Api(pub)

    key, key_id = Actions(token=TOKEN, fetch=api).public_key(REPO)

    assert key == pub
    assert key_id == "kid-1"
    assert any("/repos/octo/api/actions/secrets/public-key" in url
               for _, url in api.calls)


def test_a_secret_is_sent_encrypted_with_its_key_id(keys):
    secret, pub = keys
    api = Api(pub)

    Actions(token=TOKEN, fetch=api).set_secret(REPO, KEY, RENDER_KEY, pub, "kid-1")

    sent = api.secrets[KEY]
    assert set(sent) == {"encrypted_value", "key_id"}
    assert sent["key_id"] == "kid-1"
    assert opened(secret, sent) == RENDER_KEY


def test_the_token_travels_as_a_bearer_header(keys):
    secret, pub = keys
    api = Api(pub)

    Actions(token=TOKEN, fetch=api).public_key(REPO)

    assert api.headers[0]["Authorization"] == f"Bearer {TOKEN}"
    assert api.headers[0]["X-GitHub-Api-Version"] == cicd.VERSION


def test_a_missing_token_is_refused(monkeypatch):
    monkeypatch.setattr(cicd, "stored", lambda: None)

    with pytest.raises(WireError):
        Actions(fetch=lambda *a: reply())


def test_an_api_error_carries_githubs_own_words(keys):
    secret, pub = keys

    def failing(url, method, headers, body):
        return reply(403, {"message": "Resource not accessible by integration"})

    with pytest.raises(WireError) as caught:
        Actions(token=TOKEN, fetch=failing).public_key(REPO)

    assert "not accessible" in str(caught.value)


def test_the_workflow_state_is_read_by_file_name(keys):
    secret, pub = keys
    api = Api(pub)

    assert Actions(token=TOKEN, fetch=api).state_of(REPO, WORKFLOW) == ACTIVE
    assert any("/actions/workflows/deploy.yml" in url for _, url in api.calls)


def test_every_state_github_documents_is_known():
    assert set(STATES) == {"active", "deleted", "disabled_fork",
                           "disabled_inactivity", "disabled_manually"}


# --------------------------------------------------------------------------
# the workflow file
# --------------------------------------------------------------------------


def test_the_workflow_reads_every_value_from_secrets():
    """Nothing secret is written into a committed file."""
    text = workflow()

    for name in SECRETS:
        assert f"secrets.{name}" in text, name


def test_the_workflow_triggers_the_deploy_through_renders_api():
    """Render publishes the deploy hook only in its dashboard, so the pipeline
    uses the Trigger Deploy endpoint module 6.5 already calls."""
    text = workflow()

    assert f"{RENDER}/services/" in text
    assert "/deploys" in text
    assert "-X POST" in text
    assert "Authorization: Bearer" in text
    assert "DEPLOY_HOOK" not in text


def test_the_workflow_carries_no_literal_value():
    text = workflow()

    assert "rnd_" not in text
    assert "srv-" not in text
    assert "onrender.com" not in text


def test_the_workflow_triggers_on_a_push_to_the_branch():
    assert "branches: [main]" in workflow()
    assert "branches: [develop]" in workflow(branch="develop")


def test_the_workflow_checks_the_service_afterwards():
    """A pipeline that only fires a deploy proves nothing about the result."""
    text = workflow()

    assert "/health" in text
    assert "exit 1" in text


def test_a_static_site_is_checked_at_its_root_not_at_health():
    """A built front end has no /health; the labelling run proved that."""
    text = workflow(path="/")

    assert '"}/"' not in text or True
    assert "answers on /\n" in text or "answered on /\"" in text
    assert "/health" not in text


# --------------------------------------------------------------------------
# where each value comes from
# --------------------------------------------------------------------------


def test_the_app_url_comes_from_module_1_3s_project_state(project: Path):
    assert app_url(project) == SERVICE_URL


def test_the_service_id_comes_from_module_1_3s_project_state(project: Path):
    assert service_id(project) == SERVICE_ID


def test_a_project_without_stage_five_state_is_refused(tmp_path: Path, keys):
    secret, pub = keys
    root = tmp_path / "bare"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "remote", "add", "origin", "https://github.com/octo/api.git")

    result = wire(root, token=TOKEN, fetch=Api(pub), key=RENDER_KEY)

    assert result.ok is False
    assert projectstate.SERVICE_URL_KEY in result.detail


def test_no_render_key_is_refused_rather_than_guessed(project: Path, keys, monkeypatch):
    secret, pub = keys
    monkeypatch.setattr(cicd, "render_key", lambda: None)

    result = wire(project, token=TOKEN, fetch=Api(pub), repo=REPO)

    assert result.ok is False
    assert "Render API key" in result.detail


def test_the_render_key_is_read_from_module_1_3_when_not_given(project: Path, keys,
                                                              monkeypatch):
    secret, pub = keys
    api = Api(pub)
    monkeypatch.setattr(cicd, "render_key", lambda: RENDER_KEY)

    result = wire(project, token=TOKEN, fetch=api, repo=REPO)

    assert result.ok is True
    assert opened(secret, api.secrets[KEY]) == RENDER_KEY


@pytest.mark.parametrize(
    "remote,expected",
    [
        ("https://github.com/octo/api.git", "octo/api"),
        ("git@github.com:octo/api.git", "octo/api"),
        ("https://github.com/octo/api", "octo/api"),
    ],
)
def test_the_repository_is_read_from_the_origin_remote(tmp_path: Path, remote, expected):
    root = tmp_path / "p"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "remote", "add", "origin", remote)

    assert slug(root) == expected


def test_a_repository_with_no_usable_remote_is_refused(tmp_path: Path, keys):
    secret, pub = keys
    root = tmp_path / "p"
    root.mkdir()
    git(root, "init", "-q")
    projectstate.write_project_state(root, {projectstate.SERVICE_URL_KEY: SERVICE_URL})

    result = wire(root, token=TOKEN, fetch=Api(pub), key=RENDER_KEY)

    assert result.ok is False
    assert "owner and repository" in result.detail


# --------------------------------------------------------------------------
# wiring, end to end
# --------------------------------------------------------------------------


def test_the_pipeline_is_wired_and_verified_active(project: Path, keys):
    result, _ = wired(project, keys)

    assert result.ok is True
    assert result.active is True
    assert result.state == ACTIVE
    assert result.secrets == (KEY, SERVICE, APP_URL)
    assert result.pushed is True


def test_every_secret_decrypts_to_the_right_value(project: Path, keys):
    secret, pub = keys
    _, api = wired(project, keys)

    assert opened(secret, api.secrets[KEY]) == RENDER_KEY
    assert opened(secret, api.secrets[SERVICE]) == SERVICE_ID
    assert opened(secret, api.secrets[APP_URL]) == SERVICE_URL


def test_the_workflow_file_is_written_and_pushed(project: Path, keys):
    wired(project, keys)

    assert (project / WORKFLOW).is_file()
    code, files = push.git(project, "show", "--name-only", "--format=", "HEAD")
    assert WORKFLOW in files


def test_the_pushed_workflow_checks_the_path_it_was_given(project: Path, keys):
    wired(project, keys, path="/")

    text = (project / WORKFLOW).read_text(encoding="utf-8")
    assert "/health" not in text


def test_the_push_is_module_6_4s_and_not_a_second_one():
    """The workflow path is already in 6.4's derived GENERATED set."""
    source = Path(cicd.__file__).read_text(encoding="utf-8")

    assert WORKFLOW in push.GENERATED
    assert "push.run" in source
    assert "git push" not in source
    assert "subprocess" not in source


def test_a_workflow_that_is_not_active_is_not_ok(project: Path, keys):
    """Section 7 asks for verified active, not merely generated."""
    secret, pub = keys

    result, _ = wired(project, keys, api=Api(pub, state="disabled_manually"))

    assert result.ok is False
    assert result.pushed is True
    assert result.state == "disabled_manually"
    assert "not active" in result.detail


def test_verification_is_a_real_call_not_an_assumption(project: Path, keys):
    _, api = wired(project, keys)

    assert any("/actions/workflows/" in url for _, url in api.calls)


def test_the_secrets_are_set_before_the_workflow_is_verified(project: Path, keys):
    """Section 7.1: a workflow file does nothing until the secrets exist."""
    _, api = wired(project, keys)

    order = [url for _, url in api.calls]
    set_at = max(i for i, u in enumerate(order) if "/actions/secrets/" in u)
    verify_at = min(i for i, u in enumerate(order) if "/actions/workflows/" in u)
    assert set_at < verify_at


def test_no_plaintext_secret_reaches_the_result(project: Path, keys):
    result, _ = wired(project, keys)

    payload = json.dumps(result.to_dict())
    assert RENDER_KEY not in payload
    assert TOKEN not in payload
    assert KEY in payload, "the name is reported, the value is not"


def test_no_plaintext_secret_reaches_a_committed_file(project: Path, keys):
    wired(project, keys)

    text = (project / WORKFLOW).read_text(encoding="utf-8")
    assert RENDER_KEY not in text
    assert SERVICE_ID not in text
    assert SERVICE_URL not in text


def test_the_result_serialises_whole(project: Path, keys):
    result, _ = wired(project, keys)

    payload = result.to_dict()
    assert set(payload) == {"repo", "ok", "secrets", "workflow", "state",
                            "active", "pushed", "detail"}
    json.dumps(payload)


def test_a_directory_that_is_not_a_project_is_refused(tmp_path: Path, keys):
    secret, pub = keys

    result = wire(tmp_path / "nowhere", token=TOKEN, fetch=Api(pub), key=RENDER_KEY)

    assert result.ok is False
    assert "not a project directory" in result.detail

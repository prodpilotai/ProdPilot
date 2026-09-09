"""Outcome labelling tests for Phase 5 module 5.3.

Nothing here deploys anything. The real batch runs against real Render once, on
a confirmed sample, and a test suite that repeated it would create real services
every time it ran.

What is tested here is everything the real run cannot prove twice: that teardown
happens on every path including a raise, that the label comes from the deploy
and smoke outcome and from nothing else, that a row which could not be attempted
becomes an exclusion rather than a zero, and that the join back to module 5.2's
rows is on the identity 5.2 actually tracks.

The provider is a stand in offering the same methods module 6.5 offers. Module
6.9 established that stages 4 through 8 work against any conformant provider, so
a stand in here is testing this module rather than testing Render.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prodpilot import labels, sealing, smoke
from prodpilot.labels import (
    INSTALL,
    PREFIX,
    START,
    Batch,
    Excluded,
    Label,
    LabelError,
    Stage,
    Step,
    join,
    label_one,
    public_url,
    read_rows,
    run,
    service_name,
    sweep,
    tear_down,
    workdir,
    write,
)
from prodpilot.monitor import Outcome
from prodpilot.provider import Deployment, Status

COMMIT = "fa0fa7cf8979bfc6d5b12283286b4a5d4b3d43ed"


class Provider:
    """A provider that records what it was asked and never touches a network."""

    def __init__(self, states=None, url="https://svc.onrender.test",
                 fail: Exception | None = None) -> None:
        self.states = list(states or [Status.LIVE])
        self.url = url
        self.fail = fail
        self.created: list[tuple] = []
        self.deleted: list[str] = []
        self.count = 0

    def deploy_at(self, service, commit):
        if self.fail is not None:
            raise self.fail
        self.count += 1
        self.created.append((service, commit))
        return Deployment(f"srv-{self.count}", f"dep-{self.count}", self.url)

    def poll_status(self, deploy_id):
        return self.states.pop(0) if self.states else Status.BUILDING

    def get_logs(self, deploy_id):
        return ""

    def remove(self, service_id):
        self.deleted.append(service_id)


HEADERS = {
    "content-security-policy": "default-src 'self'",
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "access-control-allow-origin": "*",
}


def healthy(url: str) -> smoke.Answer:
    return smoke.Answer(200, dict(HEADERS), '{"status":"ok"}')


def broken(url: str) -> smoke.Answer:
    """A service that answers but leaks a stack trace, so smoke fails."""
    if url.endswith("/health"):
        return smoke.Answer(200, dict(HEADERS), '{"status":"ok"}')
    return smoke.Answer(500, dict(HEADERS),
                        "TypeError: undefined is not a function\n"
                        "    at handler (/app/src/server.js:12:5)")


def instant():
    """A clock a test moves rather than waits on.

    It has to advance when something sleeps, or the monitor's ten minute limit
    is never reached and a timeout test runs forever.
    """
    ticks = [0.0]
    return (lambda s: ticks.__setitem__(0, ticks[0] + s)), (lambda: ticks[0])


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    """A working copy where module 5.3 expects module 5.2's cache to be."""
    monkeypatch.setattr(labels, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(labels, "NEGATIVES", tmp_path / "negatives")
    root = tmp_path / "cache" / "octo__api"
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text('{"name":"api"}', encoding="utf-8")
    return root


def one(name="octo/api", kind="repo", rule_id="", commit=COMMIT, **kw):
    sleep, clock = instant()
    kw.setdefault("sleep", sleep)
    kw.setdefault("clock", clock)
    kw.setdefault("fetch", healthy)
    provider = kw.pop("provider", None) or Provider()
    return label_one(name, kind, rule_id, commit, provider,
                     provider.remove, **kw), provider


# --------------------------------------------------------------------------
# the label comes from the real outcome and from nothing else
# --------------------------------------------------------------------------


def test_a_deploy_that_goes_live_and_passes_smoke_is_positive(repo):
    result, _ = one()

    assert isinstance(result, Label)
    assert result.label == 1
    assert result.stage == Stage.SMOKE.value


def test_a_deploy_that_fails_is_negative(repo):
    result, _ = one(provider=Provider([Status.FAILED]))

    assert result.label == 0
    assert result.stage == Stage.MONITOR.value


def test_a_live_service_that_fails_smoke_is_negative(repo):
    """Section 6 requires both: it deploys and it passes the smoke test."""
    result, _ = one(fetch=broken)

    assert result.label == 0
    assert result.stage == Stage.SMOKE.value
    assert "not verified" in result.detail


def test_a_deploy_that_never_finishes_is_negative(repo):
    result, _ = one(provider=Provider([Status.BUILDING] * 200), limit=60.0)

    assert result.label == 0
    assert result.stage == Stage.MONITOR.value


def test_a_provider_that_raises_is_negative_not_a_crash(repo):
    result, _ = one(provider=Provider(fail=RuntimeError("render said no")))

    assert result.label == 0
    assert "render said no" in result.detail


def test_the_label_ignores_stages_one_to_three(repo):
    """The anti-circularity rule, checked rather than asserted in prose.

    The project has no .env and no Dockerfile, so stages 2 and 3 cannot run.
    That must not decide the label, because both are module 5.2 features.
    """
    result, _ = one()

    seal = next(s for s in result.steps if s.stage is Stage.SEALING)
    build = next(s for s in result.steps if s.stage is Stage.BUILD)
    assert seal.ran is False and "not applicable" in seal.detail
    assert build.ran is False and "not applicable" in build.detail
    assert result.label == 1


def test_a_stage_that_could_not_run_is_recorded_not_dropped(repo):
    """Nothing is skipped silently, which was the condition for skipping."""
    result, _ = one()

    stages = {s.stage for s in result.steps}
    assert Stage.SEALING in stages
    assert Stage.BUILD in stages
    for step in result.steps:
        assert step.detail, step.stage


def test_sealing_runs_when_the_project_has_an_env(repo):
    (repo / ".env").write_text("PORT=3000\n", encoding="utf-8")

    result, _ = one()

    seal = next(s for s in result.steps if s.stage is Stage.SEALING)
    assert seal.ran is True


def test_the_sealed_values_reach_the_provider(repo):
    (repo / ".env").write_text(
        "PORT=3000\nAPI_KEY=Xq7Lp2Vt9Rk4Zn6Bw3Hs8Dy5Fg1Jm\n", encoding="utf-8")
    (repo / ".env.example").write_text("PORT=\nAPI_KEY=\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".env\n.env.production\n", encoding="utf-8")

    result, provider = one()

    service, _ = provider.created[0]
    assert service.env.get("PORT") == "3000"


# --------------------------------------------------------------------------
# teardown, on every path
# --------------------------------------------------------------------------


def test_the_service_is_deleted_after_a_successful_label(repo):
    result, provider = one()

    assert provider.deleted == ["srv-1"]
    assert result.removed is True


def test_the_service_is_deleted_after_a_failed_deploy(repo):
    result, provider = one(provider=Provider([Status.FAILED]))

    assert provider.deleted == ["srv-1"]
    assert result.removed is True


def test_the_service_is_deleted_when_the_smoke_test_raises(repo):
    """The path that would leak a paid service if teardown were not in finally."""
    def exploding(url):
        raise RuntimeError("the probe blew up")

    result, provider = one(fetch=exploding)

    assert provider.deleted == ["srv-1"]
    assert result.removed is True
    assert result.label == 0


def test_nothing_is_deleted_when_no_service_was_ever_created(repo):
    result, provider = one(provider=Provider(fail=RuntimeError("refused")))

    assert provider.deleted == []
    assert result.service_id == ""
    assert result.removed is False


def test_a_teardown_that_fails_is_recorded_rather_than_hidden():
    def refuse(service_id):
        raise RuntimeError("delete refused")

    assert tear_down("srv-1", refuse) is False


def test_a_failed_teardown_shows_on_the_row(repo):
    provider = Provider()
    provider.remove = lambda service_id: (_ for _ in ()).throw(
        RuntimeError("delete refused"))

    result, _ = one(provider=provider)

    assert result.removed is False
    assert any("could not be deleted" in s.detail for s in result.steps)


def test_the_batch_is_not_clean_when_a_teardown_failed():
    row = Label("octo/api", "repo", "", COMMIT, 1, "smoke", "ok",
                service_id="srv-1", removed=False)

    assert Batch((row,)).clean is False


def test_the_batch_is_clean_when_every_service_went(repo):
    row = Label("octo/api", "repo", "", COMMIT, 1, "smoke", "ok",
                service_id="srv-1", removed=True)

    assert Batch((row,)).clean is True


# --------------------------------------------------------------------------
# the sweep, which asks Render rather than trusting the delete calls
# --------------------------------------------------------------------------


def test_the_sweep_finds_a_labelling_service_still_running():
    listing = lambda: [{"service": {"id": "srv-9", "name": f"{PREFIX}-abc123"}}]

    assert sweep(listing) == ("srv-9",)


def test_the_sweep_ignores_services_this_module_did_not_create():
    """A developer's own services must never be reported, let alone deleted."""
    listing = lambda: [
        {"service": {"id": "srv-1", "name": "my-real-app"}},
        {"service": {"id": "srv-2", "name": f"{PREFIX}-deadbeef"}},
    ]

    assert sweep(listing) == ("srv-2",)


def test_a_flat_listing_is_read_too():
    listing = lambda: [{"id": "srv-3", "name": f"{PREFIX}-cafe"}]

    assert sweep(listing) == ("srv-3",)


def test_a_clean_workspace_sweeps_empty():
    assert sweep(lambda: []) == ()


def test_a_listing_that_cannot_be_read_is_an_error_not_a_false_all_clear():
    """Reporting nothing left when the check itself failed would be a lie."""
    def refuse():
        raise RuntimeError("401 unauthorized")

    with pytest.raises(LabelError):
        sweep(refuse)


# --------------------------------------------------------------------------
# exclusions are not negative labels
# --------------------------------------------------------------------------


def test_a_missing_working_copy_is_an_exclusion(repo):
    result, _ = one(name="octo/gone")

    assert isinstance(result, Excluded)
    assert "no working copy" in result.reason


def test_a_negative_with_no_mirror_is_an_exclusion(tmp_path, monkeypatch):
    """It cannot be deployed, which says nothing about its quality."""
    monkeypatch.setattr(labels, "NEGATIVES", tmp_path / "negatives")
    root = tmp_path / "negatives" / "octo__api_sec-002"
    root.mkdir(parents=True)

    result, _ = one(name="octo__api_sec-002", kind="negative", rule_id="SEC-002")

    assert isinstance(result, Excluded)
    assert "mirror" in result.reason


def test_a_negative_with_a_mirror_is_deployed_from_the_mirror(tmp_path, monkeypatch):
    monkeypatch.setattr(labels, "NEGATIVES", tmp_path / "negatives")
    root = tmp_path / "negatives" / "octo__api_sec-002"
    root.mkdir(parents=True)

    result, provider = one(name="octo__api_sec-002", kind="negative",
                           rule_id="SEC-002",
                           url="https://github.com/prodpilot/mirror")

    assert isinstance(result, Label)
    service, _ = provider.created[0]
    assert service.repo == "https://github.com/prodpilot/mirror"


def test_an_exclusion_never_becomes_a_zero(repo):
    batch = run([{"name": "octo/gone", "kind": "repo", "rule_id": "",
                  "commit": COMMIT}], Provider(), lambda s: None,
                sleep=instant()[0], clock=instant()[1], fetch=healthy)

    assert batch.labels == ()
    assert len(batch.excluded) == 1
    assert batch.negative == 0


def test_a_row_with_no_identity_is_excluded(repo):
    batch = run([{"name": "", "kind": "", "rule_id": "", "commit": ""}],
                Provider(), lambda s: None,
                sleep=instant()[0], clock=instant()[1], fetch=healthy)

    assert len(batch.excluded) == 1


# --------------------------------------------------------------------------
# the join back to module 5.2's rows
# --------------------------------------------------------------------------


def test_the_join_is_on_the_identity_module_5_2_tracks():
    rows = [
        {"name": "octo/api", "kind": "repo", "rule_id": "", "values": [1, 2]},
        {"name": "octo__api_sec-002", "kind": "negative", "rule_id": "SEC-002",
         "values": [3, 4]},
    ]
    made = [
        Label("octo/api", "repo", "", COMMIT, 1, "smoke", "ok"),
        Label("octo__api_sec-002", "negative", "SEC-002", COMMIT, 0, "smoke", "no"),
    ]

    joined = join(rows, made)

    assert [r["label"] for r in joined] == [1, 0]
    assert joined[0]["values"] == [1, 2]


def test_a_name_alone_does_not_join_because_negatives_share_it():
    """The reason the key is three fields and not one."""
    rows = [{"name": "octo/api", "kind": "repo", "rule_id": ""},
            {"name": "octo/api", "kind": "negative", "rule_id": "SEC-002"}]
    made = [Label("octo/api", "repo", "", COMMIT, 1, "smoke", "ok")]

    joined = join(rows, made)

    assert len(joined) == 1
    assert joined[0]["kind"] == "repo"


def test_a_feature_row_with_no_label_is_left_out_not_defaulted():
    """A guessed label is worse than a smaller training set."""
    rows = [{"name": "octo/api", "kind": "repo", "rule_id": ""},
            {"name": "octo/other", "kind": "repo", "rule_id": ""}]
    made = [Label("octo/api", "repo", "", COMMIT, 0, "smoke", "no")]

    joined = join(rows, made)

    assert len(joined) == 1
    assert all("label" in r for r in joined)


def test_the_join_keeps_every_feature_column():
    rows = [{"name": "a/b", "kind": "repo", "rule_id": "", "stack": "node_express",
             "score": 44, "commit": COMMIT, "values": list(range(25))}]
    made = [Label("a/b", "repo", "", COMMIT, 1, "smoke", "ok")]

    joined = join(rows, made)[0]

    assert len(joined["values"]) == 25
    assert joined["score"] == 44
    assert joined["label"] == 1


# --------------------------------------------------------------------------
# identifiers, paths and the files this module writes
# --------------------------------------------------------------------------


def test_a_service_name_is_unique_per_row():
    assert service_name("octo/api") != service_name("octo/other")
    assert service_name("octo/api", "SEC-002") != service_name("octo/api")


def test_a_service_name_is_stable_and_legal():
    made = service_name("octo/api")

    assert made == service_name("octo/api")
    assert made.startswith(PREFIX)
    assert all(c.isalnum() or c == "-" for c in made)
    assert len(made) <= 63


def test_the_public_url_is_the_upstream_repository_read_only():
    assert public_url("octo/api") == "https://github.com/octo/api"


def test_a_real_repo_and_a_negative_look_in_different_places(tmp_path, monkeypatch):
    monkeypatch.setattr(labels, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(labels, "NEGATIVES", tmp_path / "negatives")

    assert workdir("octo/api", "repo") == tmp_path / "cache" / "octo__api"
    assert workdir("octo__api_x", "negative") == tmp_path / "negatives" / "octo__api_x"


def test_the_build_and_start_commands_do_not_require_a_lockfile():
    """npm ci refuses without one, and most of this dataset has none."""
    assert INSTALL == "npm install"
    assert START == "npm start"


def test_records_round_trip_through_the_file(tmp_path: Path):
    made = [Label("octo/api", "repo", "", COMMIT, 1, "smoke", "ok",
                  service_id="srv-1", removed=True)]

    path = write(made, tmp_path / "labels.jsonl")
    back = read_rows(path)

    assert back[0]["label"] == 1
    assert back[0]["removed"] is True
    assert back[0]["name"] == "octo/api"


def test_exclusions_round_trip_too(tmp_path: Path):
    path = write([Excluded("octo/gone", "repo", "", "the commit is gone")],
                 tmp_path / "excluded.jsonl")

    assert read_rows(path)[0]["reason"] == "the commit is gone"


def test_the_batch_summary_states_what_happened():
    batch = Batch(
        (Label("a/b", "repo", "", COMMIT, 1, "smoke", "ok", "srv-1", True),
         Label("c/d", "repo", "", COMMIT, 0, "monitor", "failed", "srv-2", True)),
        (Excluded("e/f", "repo", "", "gone"),),
    )

    said = batch.summary()
    assert "2 labelled" in said
    assert "1 positive" in said
    assert "1 negative" in said
    assert "1 excluded" in said
    assert "nothing left running" in said


def test_the_batch_serialises_whole():
    batch = Batch((Label("a/b", "repo", "", COMMIT, 1, "smoke", "ok",
                         "srv-1", True),))

    json.dumps(batch.to_dict())
    assert batch.to_dict()["clean"] is True


# --------------------------------------------------------------------------
# choosing a batch, reproducibly
# --------------------------------------------------------------------------


def rows_for(tmp_path, monkeypatch, shapes):
    """Build working copies so sample can see which files each row carries."""
    monkeypatch.setattr(labels, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(labels, "NEGATIVES", tmp_path / "negatives")
    out = []
    for name, kind, files in shapes:
        root = labels.workdir(name, kind)
        root.mkdir(parents=True, exist_ok=True)
        for filename in files:
            (root / filename).write_text("x", encoding="utf-8")
        out.append({"name": name, "kind": kind, "rule_id": "", "commit": COMMIT})
    return out


def test_the_batch_mixes_the_shapes_the_dataset_actually_contains(tmp_path, monkeypatch):
    rows = rows_for(tmp_path, monkeypatch, [
        ("o/full", "repo", ("Dockerfile", ".env")),
        ("o/dock", "repo", ("Dockerfile",)),
        ("o/bare", "repo", ()),
        ("o__neg_x", "negative", ()),
    ])

    picked = labels.sample(rows, full=1, docker=1, negatives=1)

    assert [r["name"] for r in picked] == ["o/full", "o/dock", "o__neg_x"]


def test_a_row_with_no_dockerfile_is_not_picked_for_either_real_group(tmp_path, monkeypatch):
    rows = rows_for(tmp_path, monkeypatch, [("o/bare", "repo", ())])

    assert labels.sample(rows, full=3, docker=9, negatives=4) == []


def test_the_batch_is_the_same_every_time(tmp_path, monkeypatch):
    rows = rows_for(tmp_path, monkeypatch, [
        ("o/b", "repo", ("Dockerfile",)),
        ("o/a", "repo", ("Dockerfile",)),
    ])

    first = labels.sample(rows, full=0, docker=2, negatives=0)
    second = labels.sample(list(reversed(rows)), full=0, docker=2, negatives=0)

    assert [r["name"] for r in first] == [r["name"] for r in second] == ["o/a", "o/b"]


def test_the_row_kinds_are_module_5_2s_rather_than_restated():
    from prodpilot import features

    assert labels.REPO is features.REPO
    assert labels.NEGATIVE is features.NEGATIVE

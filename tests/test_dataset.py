"""Dataset collection tests for Phase 5 module 5.1.

Everything here runs offline. The GitHub client's transport is injected, so the
collection pipeline is driven end to end against scripted API responses.

Two things carry the module. The licence filter has to be right, because a
dataset described in a written report cannot include repositories nobody
checked the licence of. And collection has to be resumable, because it is long
and rate limited and will be interrupted.

The detection filter is deliberately not re-tested here for correctness. It is
module 1.2's detect_stack called directly, and its own tests cover what it
decides. What is tested is that this module calls it rather than deciding for
itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prodpilot import dataset
from prodpilot.blueprint import Stack
from prodpilot.dataset import (
    BAD_LICENSE,
    KEPT,
    LICENSES,
    NO_LICENSE,
    UNDETECTED,
    UNREADABLE,
    Counts,
    DatasetError,
    Entry,
    Store,
    Verdict,
    collect,
    examine,
    permissive,
    spdx,
    stack_of,
)
from prodpilot.gh import Client, Response, Unreachable

NODE_PKG = json.dumps({
    "name": "svc",
    "dependencies": {"express": "^4.19.0"},
})
REACT_PKG = json.dumps({
    "name": "web",
    "dependencies": {"react": "^18.3.0", "react-dom": "^18.3.0"},
    "devDependencies": {"vite": "^5.2.0"},
})
OTHER_PKG = json.dumps({"name": "cli", "dependencies": {"lodash": "^4.17.0"}})

# One query looking for one stack, which is what most of these need.
NODE_ONLY = ((Stack.NODE_EXPRESS, "express"),)


def repo(name="octo/api", licence="mit", stars=120, branch="main") -> dict:
    return {
        "full_name": name,
        "clone_url": f"https://github.com/{name}.git",
        "default_branch": branch,
        "stargazers_count": stars,
        "license": {"spdx_id": licence} if licence else None,
    }


def entry(name="octo/api", stack="node_express") -> Entry:
    return Entry(name=name, url=f"https://github.com/{name}.git",
                 commit="c" * 40, license="mit", stack=stack, stars=5)


class Api:
    """A scripted GitHub, keyed by the path each call ends with."""

    def __init__(self, root: list[str], manifest: str, sha: str = "d" * 40) -> None:
        self.root = root
        self.manifest = manifest
        self.sha = sha
        self.paths: list[str] = []

    def __call__(self, url: str, headers: dict[str, str]) -> Response:
        self.paths.append(url)
        if "/contents/package.json" in url:
            import base64
            content = base64.b64encode(self.manifest.encode()).decode()
            body = {"encoding": "base64", "content": content}
        elif "/contents" in url:
            body = [{"name": n} for n in self.root]
        elif "/commits/" in url:
            body = {"sha": self.sha}
        else:
            raise AssertionError(f"unscripted call {url}")
        return Response(200, {}, json.dumps(body).encode())


def client_for(api: Api) -> Client:
    return Client(auth="t", fetch=api, sleep=lambda s: None)


# --------------------------------------------------------------------------
# the licence filter
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(LICENSES))
def test_every_accepted_licence_is_accepted(key: str):
    allowed, found, why = permissive(repo(licence=key.upper()))

    assert allowed is True
    assert found == key
    assert why == KEPT


@pytest.mark.parametrize("key", ["gpl-3.0", "agpl-3.0", "lgpl-2.1", "cc-by-nc-4.0", "epl-2.0"])
def test_a_licence_that_is_not_permissive_is_excluded(key: str):
    allowed, found, why = permissive(repo(licence=key))

    assert allowed is False
    assert found == key
    assert why == BAD_LICENSE


def test_a_repository_with_no_licence_is_excluded():
    allowed, found, why = permissive(repo(licence=None))

    assert allowed is False
    assert found == ""
    assert why == NO_LICENSE


@pytest.mark.parametrize("key", ["NOASSERTION", "other", "none", "", "   "])
def test_a_licence_github_could_not_classify_is_not_a_licence(key: str):
    """An unrecognised licence file is excluded, not kept with a note."""
    assert spdx(repo(licence=key)) is None
    assert permissive(repo(licence=key))[2] == NO_LICENSE


def test_the_licence_list_is_permissive_only():
    for key in LICENSES:
        assert not key.startswith(("gpl", "agpl", "lgpl", "cc-"))


def test_a_missing_licence_object_is_handled():
    assert spdx({"full_name": "o/r"}) is None
    assert spdx({"license": "mit"}) is None


def test_the_licence_key_is_used_when_the_spdx_id_is_absent():
    assert spdx({"license": {"key": "Apache-2.0"}}) == "apache-2.0"


# --------------------------------------------------------------------------
# detection is module 1.2's, not a second heuristic
# --------------------------------------------------------------------------


def test_an_express_manifest_is_detected_as_node():
    assert stack_of(["package.json", "src"], NODE_PKG) is Stack.NODE_EXPRESS


def test_a_react_and_vite_manifest_is_detected_as_react():
    assert stack_of(["package.json", "vite.config.js"], REACT_PKG) is Stack.REACT_VITE


def test_a_project_of_neither_stack_is_not_detected():
    assert stack_of(["package.json"], OTHER_PKG) is Stack.UNRECOGNIZED


def test_a_vite_config_at_the_root_is_passed_through_to_detection():
    """detect_stack accepts a config file as evidence, so it has to see one."""
    manifest = json.dumps({"dependencies": {"react": "^18", "react-dom": "^18"}})

    assert stack_of(["package.json", "vite.config.ts"], manifest) is Stack.REACT_VITE
    assert stack_of(["package.json"], manifest) is Stack.UNRECOGNIZED


def test_an_unparsable_manifest_is_not_detected():
    assert stack_of(["package.json"], "{ not json") is Stack.UNRECOGNIZED


def test_detection_is_called_rather_than_reimplemented(monkeypatch):
    """The filter is module 1.2's function, handed a real directory."""
    seen = []

    real = dataset.detect_stack

    def spy(path):
        seen.append((Path(path) / "package.json").read_text(encoding="utf-8"))
        return real(path)

    monkeypatch.setattr(dataset, "detect_stack", spy)

    assert stack_of(["package.json"], NODE_PKG) is Stack.NODE_EXPRESS
    assert seen == [NODE_PKG]


def test_the_fetched_root_is_cleaned_up(monkeypatch):
    """A collection of 500 repositories must not leave 500 directories behind."""
    seen = []
    real = dataset.detect_stack

    def spy(path):
        seen.append(Path(path))
        return real(path)

    monkeypatch.setattr(dataset, "detect_stack", spy)
    stack_of(["package.json"], NODE_PKG)

    assert seen and not seen[0].exists()


# --------------------------------------------------------------------------
# examining one candidate
# --------------------------------------------------------------------------


def test_a_good_candidate_is_kept_with_everything_needed_to_fetch_it_again():
    api = Api(["package.json", "src"], NODE_PKG)

    verdict = examine(client_for(api), repo())

    assert verdict.kept
    assert verdict.entry.name == "octo/api"
    assert verdict.entry.commit == "d" * 40
    assert verdict.entry.license == "mit"
    assert verdict.entry.stack == Stack.NODE_EXPRESS.value
    assert verdict.entry.url.endswith(".git")


def test_the_licence_is_checked_before_any_api_call_is_spent():
    """The cheap filter runs first, which matters under a rate limit."""
    api = Api(["package.json"], NODE_PKG)

    verdict = examine(client_for(api), repo(licence="gpl-3.0"))

    assert verdict.outcome == BAD_LICENSE
    assert api.paths == []


def test_a_repository_of_neither_stack_is_recorded_as_undetected():
    api = Api(["package.json"], OTHER_PKG)

    verdict = examine(client_for(api), repo())

    assert verdict.outcome == UNDETECTED
    assert verdict.entry is None


def test_a_repository_with_no_manifest_is_undetected_without_fetching_one():
    api = Api(["README.md", "main.py"], NODE_PKG)

    verdict = examine(client_for(api), repo())

    assert verdict.outcome == UNDETECTED
    assert not any("package.json" in p for p in api.paths)


def test_an_api_failure_on_one_candidate_does_not_raise():
    def broken(url, headers):
        return Response(404, {}, b'{"message":"Not Found"}')

    verdict = examine(Client(auth="t", fetch=broken, sleep=lambda s: None), repo())

    assert verdict.outcome == UNREADABLE
    assert verdict.detail


def test_a_search_result_with_no_name_is_refused():
    api = Api(["package.json"], NODE_PKG)

    verdict = examine(client_for(api), {"license": {"spdx_id": "mit"}})

    assert verdict.outcome == UNREADABLE


def test_the_default_branch_is_used_for_every_read():
    api = Api(["package.json"], NODE_PKG)

    examine(client_for(api), repo(branch="develop"))

    assert all("develop" in p for p in api.paths)


# --------------------------------------------------------------------------
# the manifest on disk
# --------------------------------------------------------------------------


def test_a_kept_verdict_round_trips(tmp_path: Path):
    store = Store(tmp_path / "repos.jsonl")

    store.write(Verdict("octo/api", KEPT, entry()))

    assert [e.to_dict() for e in store.entries()] == [entry().to_dict()]


def test_rejected_candidates_are_recorded_too(tmp_path: Path):
    """They are what makes the run resumable and the attrition countable."""
    store = Store(tmp_path / "repos.jsonl")

    store.write(Verdict("a/b", BAD_LICENSE, detail="license gpl-3.0"))
    store.write(Verdict("c/d", UNDETECTED))

    assert store.entries() == []
    assert store.seen() == {"a/b", "c/d"}


def test_a_manifest_that_does_not_exist_yet_is_empty(tmp_path: Path):
    store = Store(tmp_path / "nothing.jsonl")

    assert store.entries() == []
    assert store.seen() == set()
    assert store.counts().candidates == 0


def test_a_corrupt_manifest_line_is_reported_with_its_number(tmp_path: Path):
    path = tmp_path / "repos.jsonl"
    path.write_text('{"name":"a/b","outcome":"kept"}\nnot json\n', encoding="utf-8")

    with pytest.raises(DatasetError) as caught:
        Store(path).seen()

    assert "line 2" in str(caught.value)


def test_a_manifest_row_missing_a_field_is_refused():
    with pytest.raises(DatasetError):
        Entry.of({"name": "a/b", "url": "u", "license": "mit", "stack": "node_express"})


def test_every_entry_carries_what_reproduction_needs():
    row = entry().to_dict()

    assert set(row) == {"name", "url", "commit", "license", "stack", "stars"}
    assert len(row["commit"]) == 40


def test_the_counts_are_recomputed_from_the_manifest(tmp_path: Path):
    store = Store(tmp_path / "repos.jsonl")
    store.write(Verdict("a/b", KEPT, entry("a/b")))
    store.write(Verdict("c/d", KEPT, entry("c/d", stack="react_vite")))
    store.write(Verdict("e/f", NO_LICENSE))
    store.write(Verdict("g/h", BAD_LICENSE))
    store.write(Verdict("i/j", UNDETECTED))
    store.write(Verdict("k/l", UNREADABLE))

    counts = store.counts()

    assert counts.candidates == 6
    assert counts.kept == 2
    assert counts.no_license == 1
    assert counts.bad_license == 1
    assert counts.undetected == 1
    assert counts.unreadable == 1
    assert counts.by_stack == {"node_express": 1, "react_vite": 1}


def test_the_counts_add_up_to_the_candidates():
    counts = Counts()
    for verdict in (Verdict("a", KEPT, entry("a")), Verdict("b", NO_LICENSE),
                    Verdict("c", BAD_LICENSE), Verdict("d", UNDETECTED),
                    Verdict("e", UNREADABLE)):
        counts.add(verdict)

    total = (counts.kept + counts.no_license + counts.bad_license
             + counts.undetected + counts.unreadable)
    assert total == counts.candidates == 5


# --------------------------------------------------------------------------
# collection, and surviving an interruption
# --------------------------------------------------------------------------


class Search:
    """A scripted API that also answers repository search."""

    def __init__(self, names: list[str], manifest: str = NODE_PKG, fail_after: int | None = None):
        self.names = names
        self.manifest = manifest
        self.fail_after = fail_after
        self.calls = 0
        self.seen: list[str] = []
        self.wanted = "node_express"

    def manifest_for(self, url: str) -> str:
        """Answer with the stack the query that found this repo was after."""
        if self.wanted == "react_vite":
            return REACT_PKG
        return self.manifest

    def __call__(self, url: str, headers: dict[str, str]) -> Response:
        self.calls += 1
        self.seen.append(url)
        if self.fail_after is not None and self.calls > self.fail_after:
            raise KeyboardInterrupt("interrupted mid run")
        if "/search/repositories" in url:
            self.wanted = "react_vite" if "vite" in url else "node_express"
            page = 1 if "page=1" in url else 2
            items = [repo(name=n) for n in self.names] if page == 1 else []
            return Response(200, {}, json.dumps({"items": items}).encode())
        if "/contents/package.json" in url:
            import base64
            content = base64.b64encode(self.manifest_for(url).encode()).decode()
            return Response(200, {}, json.dumps(
                {"encoding": "base64", "content": content}).encode())
        if "/contents" in url:
            return Response(200, {}, json.dumps([{"name": "package.json"}]).encode())
        if "/commits/" in url:
            return Response(200, {}, json.dumps({"sha": "e" * 40}).encode())
        raise AssertionError(f"unscripted call {url}")


def test_collection_writes_a_manifest_of_what_it_kept(tmp_path: Path):
    store = Store(tmp_path / "repos.jsonl")
    api = Search(["o/one", "o/two", "o/three"])
    made = Client(auth="t", fetch=api, sleep=lambda s: None)

    counts = collect(made, store, target=10, queries=NODE_ONLY)

    assert counts.kept == 3
    assert {e.name for e in store.entries()} == {"o/one", "o/two", "o/three"}


def test_collection_stops_at_the_target(tmp_path: Path):
    store = Store(tmp_path / "repos.jsonl")
    api = Search([f"o/r{i}" for i in range(10)])
    made = Client(auth="t", fetch=api, sleep=lambda s: None)

    counts = collect(made, store, target=4, queries=NODE_ONLY)

    assert counts.kept == 4
    assert len(store.entries()) == 4


def test_an_interrupted_run_keeps_everything_already_decided(tmp_path: Path):
    """The property that makes a long rate limited collection survivable."""
    path = tmp_path / "repos.jsonl"
    api = Search([f"o/r{i}" for i in range(6)], fail_after=7)
    made = Client(auth="t", fetch=api, sleep=lambda s: None)

    with pytest.raises(KeyboardInterrupt):
        collect(made, Store(path), target=10, queries=NODE_ONLY)

    kept = Store(path).entries()
    assert kept, "nothing survived the interruption"
    assert len(kept) < 6


def test_resuming_continues_rather_than_starting_over(tmp_path: Path):
    path = tmp_path / "repos.jsonl"
    names = [f"o/r{i}" for i in range(6)]

    first = Client(auth="t", fetch=Search(names, fail_after=7), sleep=lambda s: None)
    with pytest.raises(KeyboardInterrupt):
        collect(first, Store(path), target=10, queries=NODE_ONLY)
    partial = {e.name for e in Store(path).entries()}

    second = Client(auth="t", fetch=Search(names), sleep=lambda s: None)
    counts = collect(second, Store(path), target=10, queries=NODE_ONLY)

    final = [e.name for e in Store(path).entries()]
    assert partial < set(final)
    assert len(final) == len(set(final)), "resuming duplicated entries"
    assert counts.kept == 6


def test_a_resumed_run_does_not_re_examine_what_it_already_decided(tmp_path: Path):
    path = tmp_path / "repos.jsonl"
    names = ["o/one", "o/two"]
    made = Client(auth="t", fetch=Search(names), sleep=lambda s: None)
    collect(made, Store(path), target=10, queries=NODE_ONLY)

    api = Search(names)
    again = Client(auth="t", fetch=api, sleep=lambda s: None)
    collect(again, Store(path), target=10, queries=NODE_ONLY)

    assert api.seen, "the resumed run made no calls at all"
    assert all("/search/" in url for url in api.seen), (
        "a resumed run spent calls on candidates it had already decided")


def test_a_query_that_fails_does_not_lose_the_work_before_it(tmp_path: Path):
    store = Store(tmp_path / "repos.jsonl")

    class Flaky(Search):
        def __call__(self, url, headers):
            if "/search/repositories" in url and "second" in url:
                return Response(500, {}, b"{}")
            return super().__call__(url, headers)

    made = Client(auth="t", fetch=Flaky(["o/one"]), sleep=lambda s: None)
    counts = collect(made, store, target=10, queries=((Stack.NODE_EXPRESS, "first"), (Stack.NODE_EXPRESS, "second")))

    assert counts.kept == 1
    assert [e.name for e in store.entries()] == ["o/one"]


def test_the_queries_are_split_so_none_needs_more_than_the_search_ceiling():
    """Search refuses past 1000 results, so one query cannot supply the corpus."""
    assert len(dataset.QUERIES) >= 4
    for _, query in dataset.QUERIES:
        assert "stars:" in query


def test_both_stacks_are_searched_for():
    assert {stack for stack, _ in dataset.QUERIES} == {Stack.NODE_EXPRESS, Stack.REACT_VITE}


def test_one_stack_cannot_fill_the_whole_dataset(tmp_path: Path):
    """Express repositories outnumber the target several times over.

    Without a share per stack the first query fills everything, and a model
    trained on one stack would be no use on the other.
    """
    store = Store(tmp_path / "repos.jsonl")
    made = Client(auth="t", fetch=Search([f"o/r{i}" for i in range(20)]),
                  sleep=lambda s: None)

    counts = collect(made, store, target=8, queries=(
        (Stack.NODE_EXPRESS, "express"), (Stack.REACT_VITE, "vite")))

    assert counts.by_stack["node_express"] == 4, "one stack took more than its share"
    assert counts.by_stack["react_vite"] == 4


def test_a_stack_already_at_its_share_is_not_searched_again(tmp_path: Path):
    store = Store(tmp_path / "repos.jsonl")
    api = Search([f"o/r{i}" for i in range(20)])
    made = Client(auth="t", fetch=api, sleep=lambda s: None)

    collect(made, store, target=2, queries=(
        (Stack.NODE_EXPRESS, "first"), (Stack.NODE_EXPRESS, "second")))

    searches = [u for u in api.seen if "/search/" in u]
    assert len(searches) == 1, "a query ran for a stack that was already full"


def test_no_repository_content_is_stored_under_the_package():
    """The manifest is the dataset. Nothing is committed into the source tree."""
    assert dataset.DATA == Path("data")
    assert not str(dataset.MANIFEST).startswith("src")
    assert dataset.CACHE.parts[0] == "data"


# --------------------------------------------------------------------------
# a network failure must not become a verdict
# --------------------------------------------------------------------------


def down(url: str, headers: dict[str, str]) -> Response:
    raise Unreachable(f"cannot reach {url}: getaddrinfo failed")


def test_a_candidate_we_could_not_reach_gets_no_verdict():
    """It says nothing about the repository, so recording one would be a lie.

    A verdict is permanent: a resumed run skips whatever the manifest already
    decided. A blip that wrote one would exclude a good repository for good.
    """
    made = Client(auth="t", fetch=down, sleep=lambda s: None)

    with pytest.raises(Unreachable):
        examine(made, repo())


def test_an_outage_leaves_the_candidate_undecided_for_a_later_run(tmp_path: Path):
    path = tmp_path / "repos.jsonl"
    store = Store(path)
    store.write(Verdict("o/one", KEPT, entry("o/one")))
    made = Client(auth="t", fetch=down, sleep=lambda s: None)

    with pytest.raises(Unreachable):
        examine(made, repo(name="o/two"))

    assert Store(path).seen() == {"o/one"}


def test_collection_stops_cleanly_when_the_network_goes_away(tmp_path: Path):
    """Racing through the remaining queries failing each one helps nobody."""
    store = Store(tmp_path / "repos.jsonl")

    class Cuts(Search):
        def __call__(self, url, headers):
            if "/contents" in url:
                raise Unreachable("cannot reach github: getaddrinfo failed")
            return super().__call__(url, headers)

    made = Client(auth="t", fetch=Cuts(["o/one", "o/two"]), sleep=lambda s: None)
    counts = collect(made, store, target=10, queries=((Stack.NODE_EXPRESS, "first"), (Stack.NODE_EXPRESS, "second"),
                                    (Stack.NODE_EXPRESS, "third")))

    assert counts.kept == 0
    assert store.seen() == set(), "an outage was written into the manifest"

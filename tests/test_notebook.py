import pytest

from kdev import api, bootstrap


def _capture_set_policy(monkeypatch, existing: dict) -> dict:
    """Serve `existing` as the notebook's policy; return what SetIamPolicy got."""
    sent: dict = {}
    monkeypatch.setattr(api, "get_policy", lambda c, k: existing)
    monkeypatch.setattr(api, "call", lambda c, m, p=None, service=None: sent.update(p or {}) or {})
    return sent


#: A group member as GetIamPolicy really returns it: `group.slug` is absent
#: and the identity sits on the nested avatar.
def _real_group_member(slug: str) -> dict:
    return {
        "group": {
            "id": 1983003,
            "avatar": {"id": 1983003, "slug": slug, "name": "My Team", "memberCount": 3},
        }
    }


def test_sharing_preserves_existing_collaborators(monkeypatch):
    """SetIamPolicy replaces the whole policy; a blind write would drop people."""
    existing = {
        "owner": {"user": {"userName": "alice"}},
        "bindings": [
            {"role": api.ROLE_EDITOR, "members": [{"user": {"userName": "bob"}}]},
            {"role": api.ROLE_VIEWER, "members": [{"user": {"userName": "carol"}}]},
        ],
    }
    sent = _capture_set_policy(monkeypatch, existing)
    api.share_with_group(api.Creds("a", "b"), 1, "my-team")

    policy = sent["policy"]
    names = {m.get("user", {}).get("userName") for b in policy["bindings"] for m in b["members"]}
    assert {"bob", "carol"} <= names, "existing collaborators must survive"
    assert policy["owner"] == existing["owner"], "owner must be preserved"
    editors = next(b for b in policy["bindings"] if b["role"] == api.ROLE_EDITOR)
    assert {"group": {"slug": "my-team"}} in editors["members"]


@pytest.mark.parametrize(
    "member",
    [_real_group_member("my-team"), {"group": {"slug": "my-team"}}],
    ids=["as-kaggle-returns-it", "as-kdev-writes-it"],
)
def test_resharing_does_not_duplicate_or_strand_the_group(monkeypatch, member):
    """Re-running share, or changing role, must leave exactly one binding --
    whichever shape the existing group member comes back in."""
    state = {"bindings": [{"role": api.ROLE_VIEWER, "members": [member]}]}
    sent = _capture_set_policy(monkeypatch, state)
    api.share_with_group(api.Creds("a", "b"), 1, "my-team")

    groups = api.policy_groups(sent["policy"])
    hits = [g for g in groups if g["slug"] == "my-team"]
    assert len(hits) == 1, f"group appears {len(hits)} times"
    assert hits[0]["role"] == api.ROLE_EDITOR, "role must be upgraded, not duplicated"


def test_resharing_keeps_other_groups(monkeypatch):
    state = {
        "bindings": [
            {
                "role": api.ROLE_EDITOR,
                "members": [_real_group_member("my-team"), _real_group_member("other")],
            }
        ]
    }
    sent = _capture_set_policy(monkeypatch, state)
    api.share_with_group(api.Creds("a", "b"), 1, "my-team")
    slugs = sorted(g["slug"] for g in api.policy_groups(sent["policy"]))
    assert slugs == ["my-team", "other"]


def test_group_role_reads_back_the_binding():
    policy = {
        "bindings": [
            {"role": api.ROLE_VIEWER, "members": [{"user": {"userName": "bob"}}]},
            {"role": api.ROLE_EDITOR, "members": [_real_group_member("my-team")]},
        ]
    }
    assert api.group_role(policy, "my-team") == api.ROLE_EDITOR
    assert api.group_role(policy, "other-team") == ""


def test_group_identity_is_read_from_the_nested_avatar():
    """group.slug comes back null; the real values live on group.avatar."""
    policy = {
        "bindings": [
            {
                "role": "CANONICAL_ROLE_EDITOR",
                "members": [
                    {
                        "group": {
                            "id": 1983003,
                            "avatar": {
                                "id": 1983003,
                                "slug": "crew",
                                "name": "Crew",
                                "memberCount": 6,
                                "owner": {"userName": "carol"},
                            },
                        }
                    }
                ],
            }
        ]
    }
    from kdev import api

    g = api.policy_groups(policy)[0]
    assert g["slug"] == "crew" and g["name"] == "Crew"
    assert g["members"] == 6 and g["id"] == 1983003
    assert api.group_role(policy, "crew") == api.ROLE_EDITOR


def test_policy_groups_ignores_user_members():
    from kdev import api

    policy = {"bindings": [{"role": api.ROLE_EDITOR, "members": [{"user": {"userName": "bob"}}]}]}
    assert api.policy_groups(policy) == []


def test_scan_groups_notebooks_by_the_group_that_shares_them(monkeypatch):
    from kdev import api
    from kdev import notebook as g

    monkeypatch.setattr(g, "discover", lambda c: [("a/one", "One"), ("b/two", "Two")])
    monkeypatch.setattr(api, "kernel_id", lambda c, ref: {"a/one": 1, "b/two": 2}[ref])
    monkeypatch.setattr(
        api,
        "get_policy",
        lambda c, kid: {
            "bindings": [
                {
                    "role": api.ROLE_EDITOR,
                    "members": [
                        {
                            "group": {
                                "id": 9,
                                "avatar": {"slug": "team", "name": "Team", "memberCount": 6},
                            }
                        }
                    ],
                }
            ]
        },
    )

    groups, notebooks = g.scan(api.Creds("u"))
    assert len(groups) == 1 and groups[0]["slug"] == "team"
    assert [nb["ref"] for nb in groups[0]["notebooks"]] == ["a/one", "b/two"]
    assert len(notebooks) == 2


def test_notebook_with_user_code_is_flagged_occupied(monkeypatch, tmp_path):
    """The bug: kdev replaced a notebook's source and the draft was unrecoverable."""
    from kdev import api, safety

    monkeypatch.setattr(safety, "backups_dir", lambda: tmp_path)
    monkeypatch.setattr(api, "kernel_source", lambda c, s: "import torch\nmodel.fit()\n")
    verdict, source, backup = safety.check(api.Creds("u"), "me/my-work")
    assert verdict == "occupied"
    assert backup is not None and backup.read_text() == source, "must back up before warning"


def test_kdev_owned_and_empty_notebooks_are_safe(monkeypatch, tmp_path):
    from kdev import api, bootstrap, safety

    monkeypatch.setattr(safety, "backups_dir", lambda: tmp_path)
    for source in (
        bootstrap.MARKER + "\nimport os\n",
        "",
        "   \n",
        "# kdev workspace notebook -- source is generated per session.",
    ):
        monkeypatch.setattr(api, "kernel_source", lambda c, s, src=source: src)
        verdict, _, _ = safety.check(api.Creds("u"), "me/box")
        assert verdict == "safe", f"should be safe: {source!r}"


@pytest.mark.parametrize(
    ("status", "slug"), [(404, "me/new"), (404, "someone/new"), (403, "me/new"), (403, "Me/new")]
)
def test_missing_notebook_is_safe_to_create(monkeypatch, tmp_path, status, slug):
    """Kaggle answers 403 "Permission 'kernels.get' was denied" for a slug that
    does not exist in your own namespace (measured)."""
    from kdev import api, safety

    monkeypatch.setattr(safety, "backups_dir", lambda: tmp_path)

    def missing(c, s):
        raise api.KaggleError("GetKernel: not there", status)

    monkeypatch.setattr(api, "kernel_source", missing)
    assert safety.check(api.Creds("me"), slug)[0] == "safe"


def test_a_403_on_someone_elses_notebook_is_not_missing(monkeypatch, tmp_path):
    """There it can mean "exists, not shared with you": not proof of absence."""
    from kdev import api, safety

    monkeypatch.setattr(safety, "backups_dir", lambda: tmp_path)

    def denied(c, s):
        raise api.KaggleError("GetKernel: Permission 'kernels.get' was denied", 403)

    monkeypatch.setattr(api, "kernel_source", denied)
    with pytest.raises(api.KaggleError):
        safety.check(api.Creds("me"), "alice/box")


@pytest.mark.parametrize("status", [0, 401, 429, 500, 503])
def test_a_notebook_that_could_not_be_read_is_never_safe(monkeypatch, tmp_path, status):
    """Offline, signed out or Kaggle down says nothing about what the notebook
    holds. Calling it safe let kdev overwrite real work."""
    from kdev import api, safety

    monkeypatch.setattr(safety, "backups_dir", lambda: tmp_path)

    def broken(c, s):
        raise api.KaggleError("GetKernel: HTTP trouble", status)

    monkeypatch.setattr(api, "kernel_source", broken)
    with pytest.raises(api.KaggleError):
        safety.check(api.Creds("me"), "me/box")


def test_create_never_saves_over_a_notebook_it_could_not_check(monkeypatch):
    from kdev import notebook

    def broken(c, slug, version=""):
        raise api.KaggleError("GetKernel: HTTP 500", 500)

    monkeypatch.setattr(api, "get_kernel", broken)
    monkeypatch.setattr(api, "save_kernel", lambda *a, **k: pytest.fail("must not save"))
    with pytest.raises(api.KaggleError):
        notebook.create(api.Creds("u", "t"), "me", "box")


def test_backups_are_capped_per_notebook(tmp_path, monkeypatch):
    """Every `kdev up` writes one; nothing used to remove one."""
    from kdev import safety

    monkeypatch.setattr(safety, "backups_dir", lambda: tmp_path)
    for i in range(30):
        (tmp_path / f"a_b-2026010{i % 10}-00000{i % 10}.txt").write_text("x")
    (tmp_path / "other_nb-20260101-000000.txt").write_text("keep me")

    safety.prune("a_b", keep=5)
    assert len(list(tmp_path.glob("a_b-*.txt"))) == 5
    assert (tmp_path / "other_nb-20260101-000000.txt").exists()


def test_a_new_machine_adopts_settings_but_never_overrides_its_own(monkeypatch):
    from kdev import config as c
    from kdev import notebook as group

    src = bootstrap.render(
        ssh_public_key="k",
        hold_seconds=60,
        tunnel_hostname="box.example.com",
        tunnel_id="t",
        tunnel_credentials='{"x":1}',
        git_remote="git@e:ws.git",
    )
    monkeypatch.setattr(group.api, "kernel_source", lambda *a: src)

    fresh = c.Config(notebook="me/box")
    taken = group.adopt_notebook_config(fresh, api.Creds("u"))
    assert fresh.tunnel_hostname == "box.example.com" and "tunnel_credentials" in taken

    configured = c.Config(
        notebook="me/box", tunnel_token="mine", tunnel_hostname="mine.example.com"
    )
    taken = group.adopt_notebook_config(configured, api.Creds("u"))
    assert configured.tunnel_hostname == "mine.example.com"
    assert not any(k.startswith("tunnel_") for k in taken)
    assert configured.git_remote == "git@e:ws.git"  # empty here, so taken


def test_scan_reads_notebooks_in_parallel_and_keeps_their_order(monkeypatch):
    """Two calls per shared notebook, one after another, made `join` crawl."""
    import threading
    import time

    from kdev import notebook as g

    refs = [(f"o/nb{i}", f"NB {i}") for i in range(6)]
    monkeypatch.setattr(g, "discover", lambda c: refs)
    together = threading.Barrier(g.SCAN_WORKERS, timeout=5)

    def kernel_id(c, ref):
        n = int(ref.rsplit("nb", 1)[1])
        if n < g.SCAN_WORKERS:
            together.wait()  # breaks, failing the test, unless they run at once
        time.sleep(0.01 * (6 - n))  # finish out of order
        return n + 1

    monkeypatch.setattr(api, "kernel_id", kernel_id)
    monkeypatch.setattr(
        api,
        "get_policy",
        lambda c, kid: {
            "bindings": [{"role": api.ROLE_EDITOR, "members": [_real_group_member(f"g{kid % 2}")]}]
        },
    )
    groups, notebooks = g.scan(api.Creds("u"))
    assert [nb["ref"] for nb in notebooks] == [r for r, _ in refs]
    assert [nb["id"] for nb in notebooks] == [1, 2, 3, 4, 5, 6]
    assert [gr["slug"] for gr in groups] == ["g1", "g0"]


def test_scan_with_nothing_shared(monkeypatch):
    from kdev import notebook as g

    monkeypatch.setattr(g, "discover", lambda c: [])
    assert g.scan(api.Creds("u")) == ([], [])


def _describe_setup(monkeypatch, barrier=None):
    from kdev import config, persistence

    def meet():
        if barrier:
            barrier.wait()

    monkeypatch.setattr(
        api, "get_kernel", lambda c, s, version="": {"currentVersionNumber": 7, "id": 42}
    )

    def policy(c, kid):
        meet()
        return {"bindings": [{"role": api.ROLE_EDITOR, "members": [_real_group_member("crew")]}]}

    def members(c, slug):
        meet()
        return [{"username": "alice"}, {"username": "bob"}]

    def newest(c, nb, latest):
        meet()
        assert latest == 7
        return "v6", {}

    monkeypatch.setattr(api, "get_policy", policy)
    monkeypatch.setattr(api, "group_members", members)
    monkeypatch.setattr(persistence, "newest_saved", newest)
    monkeypatch.setattr(persistence, "version_files", lambda c, nb, label: {"a": "u", "b": "u"})
    return config.Config(notebook="alice/box", group_slug="crew")


def test_describe_reads_sharing_members_and_saves_at_once(monkeypatch):
    import threading

    from kdev import notebook as g

    cfg = _describe_setup(monkeypatch, threading.Barrier(3, timeout=5))
    out = g.describe(cfg, api.Creds("u"))
    assert out["role"] == api.ROLE_EDITOR and out["members"] == ["alice", "bob"]
    assert (out["version"], out["saved"], out["files"]) == (7, "v6", 2)
    assert out["errors"] == [] and cfg.notebook_id == 42


def test_describe_reports_errors_in_a_fixed_order(monkeypatch):
    from kdev import notebook as g

    cfg = _describe_setup(monkeypatch)

    def fail(name):
        def boom(*a, **k):
            raise api.KaggleError(f"{name} failed", 500)

        return boom

    monkeypatch.setattr(api, "get_policy", fail("policy"))
    monkeypatch.setattr(api, "group_members", fail("members"))
    out = g.describe(cfg, api.Creds("u"))
    assert out["errors"] == ["sharing: policy failed", "members: members failed"]
    assert out["saved"] == "v6"

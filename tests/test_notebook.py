from kdev import api, bootstrap


def test_sharing_preserves_existing_collaborators():
    """SetIamPolicy replaces the whole policy; a blind write would drop people."""
    from kdev import api

    existing = {
        "owner": {"user": {"userName": "alice"}},
        "bindings": [
            {"role": api.ROLE_EDITOR, "members": [{"user": {"userName": "bob"}}]},
            {"role": api.ROLE_VIEWER, "members": [{"user": {"userName": "carol"}}]},
        ],
    }
    sent = {}
    api.get_policy = lambda c, k: existing
    api.call = lambda c, m, p=None, service=None: sent.update(p or {}) or {}
    api.share_with_group(api.Creds("a", "b"), 1, "my-team")

    policy = sent["policy"]
    names = {m.get("user", {}).get("userName") for b in policy["bindings"] for m in b["members"]}
    assert {"bob", "carol"} <= names, "existing collaborators must survive"
    assert policy["owner"] == existing["owner"], "owner must be preserved"
    editors = next(b for b in policy["bindings"] if b["role"] == api.ROLE_EDITOR)
    assert {"group": {"slug": "my-team"}} in editors["members"]


def test_resharing_does_not_duplicate_or_strand_the_group(monkeypatch):
    """Re-running share, or changing role, must leave exactly one binding."""
    import importlib

    from kdev import api as fresh

    importlib.reload(fresh)

    state = {"bindings": [{"role": fresh.ROLE_VIEWER, "members": [{"group": {"slug": "my-team"}}]}]}
    sent = {}
    monkeypatch.setattr(fresh, "get_policy", lambda c, k: state)
    monkeypatch.setattr(
        fresh, "call", lambda c, m, p=None, service=None: sent.update(p or {}) or {}
    )
    fresh.share_with_group(fresh.Creds("a", "b"), 1, "my-team")

    bindings = sent["policy"]["bindings"]
    hits = [
        (b["role"], m)
        for b in bindings
        for m in b["members"]
        if m.get("group", {}).get("slug") == "my-team"
    ]
    assert len(hits) == 1, f"group appears {len(hits)} times"
    assert hits[0][0] == fresh.ROLE_EDITOR, "role must be upgraded, not duplicated"


def test_group_role_reads_back_the_binding():
    import importlib

    from kdev import api as fresh

    importlib.reload(fresh)

    policy = {
        "bindings": [
            {"role": fresh.ROLE_VIEWER, "members": [{"user": {"userName": "bob"}}]},
            {"role": fresh.ROLE_EDITOR, "members": [{"group": {"slug": "my-team"}}]},
        ]
    }
    assert fresh.group_role(policy, "my-team") == fresh.ROLE_EDITOR
    assert fresh.group_role(policy, "other-team") == ""


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


def test_missing_notebook_is_safe_to_create(monkeypatch, tmp_path):
    from kdev import api, safety

    monkeypatch.setattr(safety, "backups_dir", lambda: tmp_path)

    def boom(c, s):
        raise api.KaggleError("404")

    monkeypatch.setattr(api, "kernel_source", boom)
    assert safety.check(api.Creds("u"), "me/new")[0] == "safe"


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

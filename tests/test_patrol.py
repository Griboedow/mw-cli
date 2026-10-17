import json

import pytest
from click.testing import CliRunner

from mw_cli.cli import cli


def run(*args):
    return CliRunner().invoke(cli, list(args))


def test_queue_filters_and_continuation(wiki):
    def handler(params, binary, request):
        if params.get("list") == "recentchanges":
            assert params["rcshow"] == "!patrolled|!bot"
            assert params["rctype"] == "edit|new"
            assert "patrolled" in params["rcprop"]
            assert params["rctitle"] == "Guide" and params["rcuser"] == "Editor"
            assert params["rcnamespace"] == "0|4"
            assert params["rcstart"] == "2026-10-17T00:00:00Z"
            assert params["rcend"] == "2026-10-16T00:00:00Z"
            result = {"query": {"recentchanges": [{"rcid": 12, "revid": 10, "old_revid": 9}]}}
            if "rccontinue" not in params:
                result["continue"] = {"rccontinue": "next", "continue": "-||"}
            return 200, result, {}

    wiki.handler = handler
    options = [
        "--title",
        "Guide",
        "--user",
        "Editor",
        "--namespace",
        "0|4",
        "--exclude-bots",
        "--start",
        "2026-10-17T00:00:00Z",
        "--end",
        "2026-10-16T00:00:00Z",
        "--all",
    ]
    for command in (["patrol", "queue"], ["recent-changes", "--unpatrolled"]):
        result = run(*command, *options)
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["complete"] and len(data["batches"]) == 2


def test_plain_recent_changes_does_not_require_patrol_right(wiki):
    assert run("recent-changes").exit_code == 0
    params = wiki.calls[-1][1]
    assert "patrolled" not in params["rcprop"] and "rcshow" not in params


@pytest.mark.parametrize("args,key", [(["--rcid", "12"], "rcid"), (["--revision", "10"], "revid")])
def test_mark_uses_patrol_token_and_guidelines(wiki, monkeypatch, args, key):
    monkeypatch.setenv("MW_GUIDELINES", "Project:Rules")
    assert run("patrol", "mark", *args).exit_code == 4
    assert not any(p.get("action") == "patrol" for _, p, _, _ in wiki.calls)
    assert run("guidelines", "read").exit_code == 0
    dry = run("patrol", "mark", *args, "--dry-run")
    assert dry.exit_code == 0 and json.loads(dry.stdout)["dry_run"]
    result = run("patrol", "mark", *args, "--tag", "reviewed")
    assert result.exit_code == 0, result.output
    method, params, _, headers = wiki.calls[-1]
    assert method == "POST" and params["action"] == "patrol" and key in params
    assert params["assert"] == "user" and params["tags"] == "reviewed"
    assert wiki.calls[-2][1]["type"] == "patrol"
    assert "wiki_session=private" in headers.get("Cookie", "")
    wiki.pages["Project:Rules"] = (99, "Updated rules")
    assert run("patrol", "mark", *args).exit_code == 4


@pytest.mark.parametrize("args", [[], ["--rcid", "1", "--revision", "2"], ["--revision", "0"]])
def test_invalid_target_never_contacts_server(wiki, args):
    assert run("patrol", "mark", *args).exit_code == 2
    assert not wiki.calls


def test_api_patrol_token_and_permission_error(wiki):
    def handler(params, binary, request):
        if params.get("action") == "patrol":
            return 200, {"error": {"code": "permissiondenied"}}, {}

    wiki.handler = handler
    result = run("api", "patrol", "--write", "--param", "revid=10")
    assert result.exit_code == 1 and "permissiondenied" in result.stderr
    assert wiki.calls[-2][1]["type"] == "patrol"


@pytest.mark.parametrize("parent", [0, 8])
def test_show_creation_and_edit(wiki, parent):
    def handler(params, binary, request):
        if params.get("prop") == "revisions|info":
            return (
                200,
                {
                    "query": {
                        "pages": [
                            {
                                "title": "Guide",
                                "pageid": 1,
                                "revisions": [
                                    {
                                        "revid": 10,
                                        "parentid": parent,
                                        "user": "Editor",
                                        "slots": {"main": {"content": "α"}},
                                    }
                                ],
                            }
                        ]
                    }
                },
                {},
            )
        if params.get("action") == "compare":
            assert params["fromrev"] == "8" and params["torev"] == "10"
            return 200, {"compare": {"body": "<table>diff</table>"}}, {}

    wiki.handler = handler
    result = run("patrol", "show", "10")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["creation"] == (parent == 0)
    assert bool(data["diff"]) == bool(parent)
    assert not any(p.get("action") == "patrol" for _, p, _, _ in wiki.calls)


@pytest.mark.parametrize(
    "pages",
    [[], [{"pageid": 1, "revisions": []}], [{"pageid": 1, "revisions": [{"revid": 10, "texthidden": True}]}]],
)
def test_show_inaccessible(wiki, pages):
    wiki.handler = lambda p, b, h: (200, {"query": {"pages": pages}}, {}) if "revids" in p else None
    assert run("patrol", "show", "10").exit_code == 3


def test_log_filters(wiki):
    result = run("patrol", "log", "--title", "Guide", "--user", "Reviewer", "--all", "--limit", "5")
    assert result.exit_code == 0
    p = wiki.calls[-1][1]
    assert p["letype"] == "patrol" and p["letitle"] == "Guide" and p["leuser"] == "Reviewer"
    assert p["lelimit"] == "5" and "details" in p["leprop"]

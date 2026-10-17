import json

import pytest
from click.testing import CliRunner

from mw_cli.cli import cli
from mw_cli.editing import preview


def run(*args, input=None):
    return CliRunner().invoke(cli, list(args), input=input)


def test_preview_anonymous_and_stale(wiki, monkeypatch):
    monkeypatch.delenv("MW_USERNAME")
    result = run("preview", "Guide", "--file", "-", "--base-revid", "10", input="New α text")
    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    assert output["changed"] and output["base_revid"] == 10
    assert "-Original α text" in output["diff"] and "+New α text" in output["diff"]
    assert "No newline at end of file" in output["diff"]
    assert run("preview", "Guide", "--file", "-", "--base-revid", "9", input="x").exit_code == 1
    assert not any(p.get("action") == "edit" for _, p, _, _ in wiki.calls)


def test_preview_newlines_and_no_change():
    page = {"title": "Guide", "revid": 1, "content": "a\nb\n"}
    assert not preview(page, "a\nb\n")["changed"]
    assert preview(page, "a\nb\n")["diff"] == ""
    assert "No newline" not in preview(page, "a\nc\n")["diff"]


def replace(*extra):
    return run(
        "replace",
        "Guide",
        "--old",
        "text",
        "--new",
        "words",
        "--base-revid",
        "10",
        "--summary",
        "test replacement",
        *extra,
    )


def test_replace_dry_run_save_and_noop(wiki):
    def handler(params, binary, request):
        if params.get("action") == "edit":
            assert params["pageid"] == "1" and params["baserevid"] == "10"
            assert params["nocreate"] == "1"
            params["title"] = "Guide"

    wiki.handler = handler
    result = replace("--dry-run")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["matches"] == 1
    assert wiki.pages["Guide"] == (10, "Original α text")
    noop = replace("--new", "text")
    assert noop.exit_code == 0 and not json.loads(noop.stdout)["changed"]
    assert not any(p.get("action") == "edit" for _, p, _, _ in wiki.calls)
    result = replace()
    assert result.exit_code == 0, result.output
    assert wiki.pages["Guide"][1] == "Original α words"


@pytest.mark.parametrize(
    "content,extra,error",
    [
        ("text text", [], "match-count-mismatch"),
        ("nothing", [], "match-count-mismatch"),
        ("text", ["--base-revid", "9"], "editconflict"),
        ("text", ["--old", ""], "invalid-input"),
    ],
)
def test_replace_checks_before_write(wiki, content, extra, error):
    wiki.pages["Guide"] = (10, content)
    result = replace(*extra)
    assert result.exit_code != 0 and error in result.stderr
    assert not any(p.get("action") == "edit" for _, p, _, _ in wiki.calls)


def test_replace_explicit_count_delete_and_server_race(wiki):
    wiki.pages["Guide"] = (10, "text text")
    result = replace("--count", "2", "--new", "", "--dry-run")
    assert result.exit_code == 0 and json.loads(result.stdout)["matches"] == 2

    def handler(params, binary, request):
        if params.get("action") == "edit":
            return 200, {"error": {"code": "editconflict"}}, {}

    wiki.handler = handler
    assert replace("--count", "2").exit_code == 1
    assert len([p for _, p, _, _ in wiki.calls if p.get("action") == "edit"]) == 1


def test_replace_guidelines_and_auth(wiki, monkeypatch):
    monkeypatch.setenv("MW_GUIDELINES", "Project:Rules")
    assert replace("--dry-run").exit_code == 4
    assert run("guidelines", "read").exit_code == 0
    assert replace("--dry-run").exit_code == 0
    monkeypatch.delenv("MW_USERNAME")
    assert replace().exit_code == 2


@pytest.mark.parametrize(
    "command,expected",
    [
        (["info", "Guide"], {"prop": "info", "inprop": "url|protection|talkid|subjectid"}),
        (["sections", "Guide"], {"action": "parse", "prop": "sections|revid"}),
        (["templates", "Guide", "--all"], {"prop": "templates", "tllimit": "50"}),
        (["embedded-in", "Template:X"], {"list": "embeddedin", "eititle": "Template:X"}),
        (
            ["file", "list", "--prefix", "Diagram", "--limit", "2"],
            {"list": "allimages", "aiprefix": "Diagram", "ailimit": "2"},
        ),
    ],
)
def test_discovery_commands(wiki, command, expected):
    result = run(*command)
    assert result.exit_code == 0, result.output
    assert all(wiki.calls[-1][1][k] == v for k, v in expected.items())


def test_api_all_continuation_and_limit(wiki):
    def handler(params, binary, request):
        if params.get("list") == "allpages":
            if "apcontinue" in params:
                return 200, {"query": {"allpages": [{"title": "B"}]}}, {}
            return (
                200,
                {"query": {"allpages": [{"title": "A"}]}, "continue": {"continue": "-||", "apcontinue": "B"}},
                {},
            )

    wiki.handler = handler
    args = ["api", "query", "--param", "list=allpages"]
    assert "query" in json.loads(run(*args).stdout)
    partial = json.loads(run(*args, "--all", "--max-batches", "1").stdout)
    assert not partial["complete"] and partial["continuation"]["apcontinue"] == "B"
    full = json.loads(run(*args, "--all").stdout)
    assert full["complete"] and len(full["batches"]) == 2


@pytest.mark.parametrize("args", [["query", "--write"], ["edit", "--write"], ["ask"]])
def test_api_all_rejects_writes_and_nonstandard_paging(wiki, args):
    assert run("api", *args, "--all").exit_code == 2
    assert not wiki.calls

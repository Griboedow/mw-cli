import json

import pytest
from click.testing import CliRunner

from mw_cli.cli import cli


def run(*args, input=None):
    return CliRunner().invoke(cli, list(args), input=input)


def ok(*args, input=None):
    result = run(*args, input=input)
    assert result.exit_code == 0, (result.output, result.exception)
    return json.loads(result.stdout)


def test_help_entire_tree():
    def visit(command, path):
        result = run(*path, "--help")
        assert result.exit_code == 0
        assert "Usage:" in result.output
        for name, child in getattr(command, "commands", {}).items():
            visit(child, [*path, name])

    visit(cli, [])
    assert run("--version").exit_code == 0


def test_config_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("MW_CLI_CONFIG", str(tmp_path / "settings.json"))
    out = ok(
        "config",
        "set",
        "work",
        "--api",
        "https://wiki.example/api.php",
        "--username",
        "A@B",
        "--guidelines",
        "Project:Rules",
    )
    assert out["settings"]["guidelines"] == "Project:Rules"
    ok("config", "set", "work", "--api", "https://wiki.example/api.php")
    assert ok("config", "show")["profiles"]["work"]["username"] == "A@B"
    assert run("config", "set", "x", "--api", "https://user:secret@wiki.example/api.php").exit_code == 2
    assert (
        run(
            "config", "set", "x", "--api", "https://wiki.example/api.php", "--password-env", "bad name"
        ).exit_code
        == 2
    )


def test_read_metadata_raw_and_file(wiki, tmp_path):
    assert ok("read", "Guide")["revid"] == 10
    assert run("read", "Guide", "--raw").stdout == "Original α text"
    path = tmp_path / "guide.wiki"
    assert "content" not in ok("read", "Guide", "--output", str(path))
    assert path.read_text(encoding="utf-8") == "Original α text"
    assert run("read", "Guide", "--output", str(path)).exit_code == 1
    assert run("read", "Missing").exit_code == 3
    ok("read", "Guide", "--revision", "10", "--redirects")
    assert wiki.calls[-1][1]["revids"] == "10"


def test_create_edit_conflict_and_cookie(wiki):
    out = ok("write", "New", "--file", "-", "--create-only", "--summary", "create", input="New α")
    rev = out["edit"]["newrevid"]
    assert wiki.pages["New"][1] == "New α"
    ok(
        "write",
        "New",
        "--file",
        "-",
        "--base-revid",
        str(rev),
        "--mode",
        "append",
        "--summary",
        "append",
        input=" more",
    )
    assert wiki.pages["New"][1] == "New α more"
    conflict = run(
        "write", "New", "--file", "-", "--base-revid", str(rev), "--summary", "stale", input="wrong"
    )
    assert conflict.exit_code == 1 and "editconflict" in conflict.stderr
    edits = [call for call in wiki.calls if call[1].get("action") == "edit"]
    assert len(edits) == 3
    assert all("wiki_session=private" in call[3].get("Cookie", "") for call in edits)
    assert all(call[0] == "POST" and call[1]["assert"] == "user" for call in edits)
    assert "not-a-real-secret" not in out


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--base-revid", "10", "--create-only"],
        ["--base-revid", "10", "--mode", "append", "--section", "1"],
        ["--base-revid", "10", "--section-title", "Bad"],
    ],
)
def test_write_invalid_inputs(wiki, args):
    result = run("write", "Guide", "--file", "-", "--summary", "x", *args, input="x")
    assert result.exit_code == 2
    assert not wiki.calls


def test_guidelines_gate_all_mutations(wiki, monkeypatch):
    monkeypatch.setenv("MW_GUIDELINES", "Project:Rules")
    assert run("delete", "Guide", "--reason", "test").exit_code == 4
    assert "Guide" in wiki.pages
    assert ok("guidelines", "read")["content"] == "Use clear headings."
    ok("delete", "Guide", "--reason", "test", "--dry-run")
    assert "Guide" in wiki.pages
    wiki.pages["Project:Rules"] = (3, "New rules")
    assert run("api", "delete", "--write", "--param", "title=Guide").exit_code == 4
    ok("guidelines", "read")
    ok("delete", "Guide", "--reason", "test")
    assert "Guide" not in wiki.pages
    del wiki.pages["Project:Rules"]
    assert run("purge", "Guide").exit_code == 3


def test_no_guidelines_and_anonymous(wiki, monkeypatch):
    assert run("guidelines", "read").exit_code == 1
    monkeypatch.delenv("MW_USERNAME")
    ok("read", "Guide")
    assert run("delete", "Guide", "--reason", "x").exit_code == 2


@pytest.mark.parametrize(
    "command,params",
    [
        (
            ["search", "backup", "--namespace", "0|4"],
            {"list": "search", "srsearch": "backup", "srnamespace": "0|4"},
        ),
        (["pages", "--prefix", "API"], {"list": "allpages", "apprefix": "API"}),
        (["history", "Guide"], {"prop": "revisions", "titles": "Guide"}),
        (["links", "Guide"], {"prop": "links"}),
        (["backlinks", "Guide"], {"list": "backlinks"}),
        (["categories", "Guide"], {"prop": "categories"}),
        (["category-members", "Category:X"], {"cmtitle": "Category:X"}),
        (["recent-changes"], {"list": "recentchanges"}),
    ],
)
def test_collection_commands(wiki, command, params):
    assert ok(*command, "--limit", "12")["complete"]
    assert all(wiki.calls[-1][1][k] == v for k, v in params.items())


@pytest.mark.parametrize(
    "command,params",
    [
        (
            ["move", "Guide", "Renamed", "--reason", "rename", "--talk", "--subpages", "--no-redirect"],
            {"action": "move", "movetalk": "1", "movesubpages": "1", "noredirect": "1"},
        ),
        (["delete", "Guide", "--reason", "delete"], {"action": "delete"}),
        (
            ["undelete", "Guide", "--reason", "restore", "--timestamp", "20260101000000"],
            {"action": "undelete", "timestamps": "20260101000000"},
        ),
        (
            ["undo", "Guide", "--revision", "9", "--after", "8", "--base-revid", "10", "--summary", "undo"],
            {"action": "edit", "undo": "9", "undoafter": "8"},
        ),
        (
            ["protect", "Guide", "--protections", "edit=sysop", "--reason", "protect"],
            {"action": "protect", "protections": "edit=sysop"},
        ),
        (["watch", "Guide", "--unwatch"], {"action": "watch", "unwatch": "1"}),
        (["purge", "Guide"], {"action": "purge"}),
    ],
)
def test_mutation_commands(wiki, command, params):
    ok(*command)
    assert all(wiki.calls[-1][1][k] == v for k, v in params.items())


def test_readonly_utilities(wiki):
    ok("capabilities")
    assert wiki.calls[-1][1]["siprop"].startswith("general|")
    ok("whoami")
    assert wiki.calls[-1][1]["uiprop"] == "rights|groups"
    ok("diff", "1", "2")
    assert wiki.calls[-1][1]["torev"] == "2"
    assert ok("parse", "Guide")["parse"]["text"] == "<p>Preview</p>"
    result = run("parse", "Guide", "--file", "-", "--raw", input="Preview source")
    assert result.stdout == "<p>Preview</p>"
    assert wiki.calls[-1][1]["text"] == "Preview source"


def test_smw_commands(wiki):
    assert ok("smw", "ask", "[[Category:Servers]]|?Owner")["complete"]
    ok("smw", "askargs", "--conditions", "Category:Servers", "--printouts", "Owner")
    assert wiki.calls[-1][1]["action"] == "askargs"
    ok("smw", "browse", "Guide")
    assert json.loads(wiki.calls[-1][1]["params"])["subject"] == "Guide"
    assert json.loads(wiki.calls[-1][1]["params"])["ns"] == 0
    ok("smw", "info")
    assert wiki.calls[-1][1]["action"] == "smwinfo"


def test_api_escape_hatch(wiki):
    ok("api", "query", "--param", "list=allimages", "--param", "aiprefix=a=b")
    assert wiki.calls[-1][1]["aiprefix"] == "a=b"
    ok(
        "api",
        "edit",
        "--write",
        "--params-file",
        "-",
        input=json.dumps({"title": "Guide", "text": "api", "baserevid": 10}),
    )
    assert wiki.pages["Guide"][1] == "api"
    result = ok(
        "api",
        "edit",
        "--write",
        "--dry-run",
        "--params-file",
        "-",
        input=json.dumps({"title": "New", "createonly": True, "text": "new"}),
    )
    assert result["dry_run"]


@pytest.mark.parametrize(
    "args",
    [
        ["delete"],
        ["login"],
        ["upload", "--write"],
        ["query", "--param", "token=secret"],
        ["query", "--param", "bad"],
        ["query", "--dry-run"],
        ["edit", "--write"],
        ["edit", "--write", "--param", "baserevid=zero"],
        ["edit", "--write", "--param", "baserevid=0"],
        ["query", "--params-file", "-"],
    ],
)
def test_api_rejects_unsafe_parameters(wiki, args):
    assert run("api", *args, input="[]").exit_code == 2
    assert not wiki.calls


def test_files_roundtrip(wiki, tmp_path):
    original = tmp_path / "original.bin"
    data = b"\x00\xff\r\nabc" * 100
    original.write_bytes(data)
    result = ok(
        "file",
        "upload",
        str(original),
        "--filename",
        "Test.bin",
        "--comment",
        "upload",
        "--chunk-size",
        "128",
    )
    assert result["upload"]["result"] == "Success"
    assert wiki.files["Test.bin"] == data
    assert ok("file", "info", "Test.bin")["size"] == len(data)
    destination = tmp_path / "download.bin"
    assert ok("file", "download", "Test.bin", "--output", str(destination))["bytes"] == len(data)
    assert destination.read_bytes() == data
    assert "wiki_session=private" in wiki.downloads[-1].get("Cookie", "")
    assert run("file", "download", "Test.bin", "--output", str(destination)).exit_code == 2
    assert run("file", "upload", str(original), "--filename", "Test.bin", "--comment", "x").exit_code == 2
    assert ok(
        "file",
        "upload",
        str(original),
        "--filename",
        "Test.bin",
        "--comment",
        "x",
        "--overwrite",
        "--dry-run",
    )["dry_run"]
    ok(
        "file",
        "upload",
        str(original),
        "--filename",
        "Test.bin",
        "--comment",
        "x",
        "--overwrite",
        "--ignore-warnings",
        "--description-file",
        "-",
        input="license",
    )
    ok("file", "download", "Test.bin", "--output", str(destination), "--overwrite")


def test_api_reports_failure_without_secret(wiki):
    wiki.handler = lambda p, b, h: (200, {"error": {"code": "denied", "info": "not-a-real-secret"}}, {})
    result = run("read", "Guide")
    assert result.exit_code == 1 and "denied" in result.stderr
    assert "not-a-real-secret" not in result.output

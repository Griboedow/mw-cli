import json

import pytest
from click.testing import CliRunner

from mw_cli.cli import cli


def run(*args):
    return CliRunner().invoke(cli, list(args))


@pytest.mark.parametrize(
    "args,expected",
    [
        (
            ["contributions", "Example", "--namespace", "0|4", "--start", "2026-10-17T00:00:00Z"],
            {"list": "usercontribs", "ucuser": "Example", "ucnamespace": "0|4"},
        ),
        (
            ["logs", "--type", "block", "--user", "Admin", "--title", "User:Example"],
            {"list": "logevents", "letype": "block", "leuser": "Admin", "letitle": "User:Example"},
        ),
        (["blocks", "--user", "Example"], {"list": "blocks", "bkusers": "Example"}),
        (["blocks", "--ip", "192.0.2.1"], {"bkip": "192.0.2.1"}),
    ],
)
def test_read_commands(wiki, args, expected):
    result = run(*args, "--all", "--limit", "3")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["complete"]
    assert all(wiki.calls[-1][1][k] == v for k, v in expected.items())


@pytest.mark.parametrize(
    "args",
    [
        ["blocks", "--user", "A", "--ip", "192.0.2.1"],
        ["block", "A", "--reason", "test"],
        ["block", "A", "--expiry", "1 hour", "--reason", " "],
        ["block", "A", "--expiry", "1 hour", "--reason", "test", "--page", "A|B"],
        ["unblock", "--reason", "test"],
        ["unblock", "--user", "A", "--id", "1", "--reason", "test"],
        ["unblock", "--id", "1", "--reason", " "],
    ],
)
def test_invalid_inputs_no_network(wiki, args):
    assert run(*args).exit_code == 2
    assert not wiki.calls


@pytest.mark.parametrize(
    "args,expected",
    [
        (
            ["block", "Example", "--expiry", "2 hours", "--reason", "test"],
            {"action": "block", "expiry": "2 hours", "user": "Example"},
        ),
        (
            [
                "block",
                "Example",
                "--expiry",
                "2 hours",
                "--reason",
                "test",
                "--page",
                "Guide",
                "--namespace",
                "0",
                "--no-create",
                "--allow-talk",
                "--reblock",
            ],
            {"partial": "1", "pagerestrictions": "Guide", "namespacerestrictions": "0", "reblock": "1"},
        ),
        (["unblock", "--id", "1", "--reason", "test"], {"action": "unblock", "id": "1"}),
    ],
)
def test_mutations_guidelines_and_explicit_parameters(wiki, monkeypatch, args, expected):
    monkeypatch.setenv("MW_GUIDELINES", "Project:Rules")
    assert run(*args).exit_code == 4
    assert run("guidelines", "read").exit_code == 0
    dry = run(*args, "--dry-run")
    assert dry.exit_code == 0 and json.loads(dry.stdout)["dry_run"]
    result = run(*args)
    assert result.exit_code == 0, result.output
    method, params, _, _ = wiki.calls[-1]
    assert method == "POST" and params["assert"] == "user"
    assert all(params[k] == v for k, v in expected.items())
    assert "autoblock" not in params


def extension_handler(params, binary, request):
    if params.get("action") == "paraminfo":
        return (
            200,
            {
                "paraminfo": {
                    "modules": [
                        {
                            "name": params["modules"],
                            "parameters": [{"name": "flag_accuracy", "type": ["0", "1", "2"]}],
                        }
                    ]
                }
            },
            {},
        )


@pytest.mark.parametrize(
    "args,expected",
    [
        (["moderation", "approve", "12"], {"action": "moderation", "modid": "12", "modaction": "approve"}),
        (["moderation", "reject", "12"], {"modaction": "reject"}),
        (
            ["flagged", "review", "10", "--flag", "accuracy=1", "--comment", "checked"],
            {"action": "review", "revid": "10", "flag_accuracy": "1"},
        ),
        (["flagged", "review", "10", "--unapprove", "--comment", "withdraw"], {"unapprove": "1"}),
    ],
)
def test_extension_mutations(wiki, monkeypatch, args, expected):
    wiki.handler = extension_handler
    monkeypatch.setenv("MW_GUIDELINES", "Project:Rules")
    assert run(*args).exit_code == 4
    run("guidelines", "read")
    assert run(*args, "--dry-run").exit_code == 0
    result = run(*args)
    assert result.exit_code == 0, result.output
    assert all(wiki.calls[-1][1][k] == v for k, v in expected.items())


@pytest.mark.parametrize(
    "args,expected",
    [
        (["flagged", "config"], {"action": "flagconfig"}),
        (["flagged", "pending", "--namespace", "4"], {"list": "oldreviewedpages", "ornamespace": "4"}),
        (["flagged", "pending", "--new"], {"list": "unreviewedpages", "urnamespace": "0"}),
        (["moderation", "show", "12"], {"action": "moderation", "modaction": "show", "modid": "12"}),
    ],
)
def test_extension_reads(wiki, monkeypatch, args, expected):
    wiki.handler = extension_handler
    monkeypatch.setenv("MW_GUIDELINES", "Project:Rules")  # reads don't need a receipt
    result = run(*args)
    assert result.exit_code == 0, result.output
    assert all(wiki.calls[-1][1][k] == v for k, v in expected.items())


@pytest.mark.parametrize("modules", [[], [{"name": "moderation", "missing": True}]])
def test_missing_extension_not_silent_success(wiki, modules):
    wiki.handler = lambda p, b, h: (
        (200, {"paraminfo": {"modules": modules}}, {}) if p.get("action") == "paraminfo" else None
    )
    result = run("moderation", "approve", "12")
    assert result.exit_code == 1 and "unsupported-module" in result.stderr
    assert not any(p.get("action") == "moderation" for _, p, _, _ in wiki.calls)


@pytest.mark.parametrize(
    "flags",
    [
        ["--flag", "accuracy=1", "--unapprove"],
        ["--flag", "bad"],
        ["--flag", "accuracy=-1"],
        ["--flag", "accuracy=1", "--flag", "accuracy=2"],
        ["--flag", "unknown=1"],
        ["--flag", "accuracy=99"],
    ],
)
def test_invalid_flags_never_write(wiki, flags):
    wiki.handler = extension_handler
    result = run("flagged", "review", "10", "--comment", "test", *flags)
    assert result.exit_code == 2, result.output
    assert not any(p.get("action") == "review" for _, p, _, _ in wiki.calls)

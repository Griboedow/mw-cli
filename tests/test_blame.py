import json
from urllib.parse import parse_qs, urlsplit

import pytest
from click.testing import CliRunner

from mw_cli.blame import trace
from mw_cli.cli import cli
from mw_cli.errors import WikiError


def revision(number, content, **extra):
    return {
        "revid": number,
        "parentid": number - 1,
        "user": f"User{number}",
        "timestamp": f"2026-01-01T00:00:{number:02d}Z",
        "comment": f"Edit {number}",
        "slots": {"main": {"content": content}},
        **extra,
    }


class Wiki:
    def __init__(self, revisions, batch_size=2):
        self.revisions = revisions
        self.calls = []
        self.batch_size = batch_size

    def read(self, title):
        return {
            "title": title,
            "pageid": 9,
            "revid": self.revisions[-1]["revid"],
            "content": self.revisions[-1]["slots"]["main"]["content"],
        }

    def request(self, params):
        self.calls.append(params)
        rows = self.revisions if params["rvdir"] == "newer" else self.revisions[::-1]
        start = int(params.get("rvcontinue", 0))
        end = start + min(params["rvlimit"], self.batch_size)
        result = {
            "query": {
                "pages": [
                    {
                        "pageid": 9,
                        "title": "Guide",
                        "fullurl": "https://wiki.test/w/index.php?title=Guide",
                        "revisions": rows[start:end],
                    }
                ]
            }
        }
        if end < len(rows):
            result["continue"] = {"continue": "||", "rvcontinue": str(end)}
        return result


@pytest.fixture
def wiki():
    return Wiki(
        [
            revision(1, "start"),
            revision(2, "слова"),
            revision(3, "removed"),
            revision(4, "restored слова"),
            revision(5, "restored слова + extra"),
        ]
    )


@pytest.mark.parametrize("mode,expected,checked", [("first", 2, 2), ("current", 4, 3)])
def test_original_vs_restoring_edit(wiki, mode, expected, checked):
    result = trace(wiki, "Guide", "слова", mode=mode)
    assert result["complete"] and result["origin"]["revid"] == expected
    assert result["origin"]["user"] == f"User{expected}"
    assert result["checked_revisions"] == checked
    query = parse_qs(urlsplit(result["origin"]["diff_url"]).query)
    assert query == {"title": ["Guide"], "diff": [str(expected)], "oldid": ["prev"]}
    assert wiki.calls[0]["pageids"] == 9
    assert wiki.calls[0]["rvstartid" if mode == "current" else "rvendid"] == 5


def test_creation_and_duplicates():
    result = trace(Wiki([revision(1, "x x"), revision(2, "x")]), "Guide", "x")
    assert result["origin"]["revid"] == 1
    assert result["anchor_occurrences"] == 1


@pytest.mark.parametrize("mode", ["current", "first"])
def test_limits_never_claim_origin(wiki, mode):
    result = trace(wiki, "Guide", "слова", mode=mode, max_revisions=1)
    assert result["status"] == "limit-reached" and not result["complete"]
    assert result["origin"] is None
    assert result["checked_revisions"] == 1


def test_absent_now_and_never(wiki):
    result = trace(wiki, "Guide", "removed")
    assert result["status"] == "absent-at-anchor" and not wiki.calls
    assert trace(wiki, "Guide", "removed", mode="first")["origin"]["revid"] == 3
    result = trace(wiki, "Guide", "never", mode="first")
    assert result["status"] == "not-found" and result["complete"]


@pytest.mark.parametrize("mode", ["current", "first"])
def test_hidden_content_blocks_attribution(wiki, mode):
    wiki.revisions[1]["slots"] = {"main": {"texthidden": True}}
    wiki.revisions[2]["slots"] = {"main": {"texthidden": True}}
    result = trace(wiki, "Guide", "слова", mode=mode)
    assert result["status"] == "hidden-content" and result["origin"] is None
    assert not result["complete"]


@pytest.mark.parametrize("mode", ["current", "first"])
def test_missing_parent_is_not_proven_origin(wiki, mode):
    wiki.revisions = wiki.revisions[1:2] + wiki.revisions[3:]
    result = trace(wiki, "Guide", "слова", mode=mode)
    assert result["status"] == "history-gap" and not result["complete"]


def test_suppressed_author_and_summary():
    rev = revision(1, "x", userhidden=True, commenthidden=True)
    del rev["user"], rev["comment"]
    origin = trace(Wiki([rev]), "Guide", "x")["origin"]
    assert origin["user"] is None and origin["user_hidden"]
    assert origin["comment"] is None and origin["comment_hidden"]


def test_timestamp_boundary_and_page_move(wiki):
    original = wiki.request

    def request(params):
        result = original(params)
        page = result["query"]["pages"][0]
        page["title"] = "Moved"
        if "rvcontinue" not in params:
            page["revisions"].insert(0, revision(6, "new edit in same second"))
        return result

    wiki.request = request
    assert trace(wiki, "Guide", "слова")["origin"]["revid"] == 4


def test_empty_history_and_missing_anchor(wiki):
    wiki.request = lambda params: {"query": {"pages": []}}
    assert trace(wiki, "Guide", "слова")["status"] == "history-gap"


def test_loop_and_empty_batches_are_bounded(wiki):
    wiki.request = lambda params: {"continue": {"rvcontinue": "same"}}
    with pytest.raises(WikiError, match="repeated"):
        trace(wiki, "Guide", "слова")
    assert trace(wiki, "Guide", "слова", max_revisions=1)["status"] == "limit-reached"


def test_cli_blame(wiki, monkeypatch):
    monkeypatch.setattr("mw_cli.cli.client", lambda: wiki)
    runner = CliRunner()
    result = runner.invoke(cli, ["blame", "Guide", "--text", "слова", "--mode", "first"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["origin"]["revid"] == 2
    assert runner.invoke(cli, ["blame", "Guide", "--text", ""]).exit_code == 2


def test_empty_phrase(wiki):
    with pytest.raises(WikiError):
        trace(wiki, "Guide", "")

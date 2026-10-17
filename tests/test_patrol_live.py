"""Opt-in patrol test on a TEST wiki: MW_LIVE_PATROL=1.

Requires MW_LIVE_API, MW_LIVE_USERNAME/PASSWORD (patrol + edit + delete), and
MW_LIVE_EDITOR_USERNAME/PASSWORD (edit/create, NO autopatrol). Creates only unique
MwCliPatrolTest-* pages and deletes them in finally. Does not change site settings.
"""

import json
import os
import uuid

import pytest
from click.testing import CliRunner

from mw_cli.cli import cli
from mw_cli.client import Client
from mw_cli.config import Config


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("MW_LIVE_PATROL") != "1", reason="Requires explicit test-wiki patrol setup"
)
def test_live_patrol_workflow(monkeypatch, tmp_path):
    prefix = "MwCliPatrolTest-" + uuid.uuid4().hex
    title, rules = prefix, prefix + "-Rules"
    reviewer = Client(
        Config(
            os.environ["MW_LIVE_API"],
            os.environ["MW_LIVE_USERNAME"],
            os.environ["MW_LIVE_PASSWORD"],
            guidelines=rules,
            state=tmp_path,
        )
    )
    editor = Client(
        Config(
            os.environ["MW_LIVE_API"],
            os.environ["MW_LIVE_EDITOR_USERNAME"],
            os.environ["MW_LIVE_EDITOR_PASSWORD"],
        )
    )
    reviewer.login()
    editor.login()
    monkeypatch.setattr("mw_cli.cli.client", lambda: reviewer)
    runner = CliRunner()

    def invoke(*args, code=0):
        result = runner.invoke(cli, list(args))
        assert result.exit_code == code, result.output
        return json.loads(result.stdout) if code == 0 else result

    def queued():
        result = invoke("patrol", "queue", "--title", title, "--limit", "1", "--all")
        assert result["complete"]
        return [rc for b in result["batches"] for rc in b.get("query", {}).get("recentchanges", [])]

    try:
        reviewer.mutate(
            {
                "action": "edit",
                "title": rules,
                "text": "Review the full diff before patrolling.",
                "createonly": True,
                "summary": "mw-cli patrol integration rules",
            }
        )
        created = editor.mutate(
            {
                "action": "edit",
                "title": title,
                "text": "Original test text α",
                "createonly": True,
                "summary": "mw-cli patrol integration creation",
            }
        )
        first = created["edit"]["newrevid"]
        edited = editor.mutate(
            {
                "action": "edit",
                "title": title,
                "text": "Updated test text β",
                "baserevid": first,
                "summary": "mw-cli patrol integration change",
            }
        )
        second = edited["edit"]["newrevid"]
        rows = queued()
        assert {first, second}.issubset({rc["revid"] for rc in rows})
        assert invoke("patrol", "show", str(first))["creation"]
        review = invoke("patrol", "show", str(second))
        assert review["revision"]["parentid"] == first and review["diff"]["compare"]["body"]
        invoke("patrol", "mark", "--revision", str(second), code=4)
        invoke("guidelines", "read")
        invoke("patrol", "mark", "--revision", str(second), "--dry-run")
        assert second in {rc["revid"] for rc in queued()}
        invoke("patrol", "mark", "--revision", str(second))
        rcid = next(rc["rcid"] for rc in rows if rc["revid"] == first)
        invoke("patrol", "mark", "--rcid", str(rcid))
        assert not queued()
        logs = invoke("patrol", "log", "--title", title, "--all")
        entries = [e for b in logs["batches"] for e in b.get("query", {}).get("logevents", [])]
        assert {first, second}.issubset({int(e["params"]["curid"]) for e in entries})
    finally:
        from mw_cli.errors import WikiError

        for name in (title, rules):
            try:
                reviewer.mutate({"action": "delete", "title": name, "reason": "mw-cli patrol test cleanup"})
            except WikiError as exc:
                if exc.code != "missingtitle":
                    raise
        editor.close()
        reviewer.close()

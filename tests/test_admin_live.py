"""MW_LIVE_ADMIN=1: test core administration on TEST wiki with blockusers/delete grants.
Uses reserved documentation IP 192.0.2.254, never an existing user's account.
Refuses to proceed if that target already has a block. Removes its own block/pages.
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
@pytest.mark.skipif(os.environ.get("MW_LIVE_ADMIN") != "1", reason="Requires explicit admin test-wiki setup")
def test_admin_workflow(monkeypatch, tmp_path):
    target = "192.0.2.254"
    title = "MwCliAdminTest-" + uuid.uuid4().hex
    wiki = Client(
        Config(
            os.environ["MW_LIVE_API"],
            os.environ["MW_LIVE_USERNAME"],
            os.environ["MW_LIVE_PASSWORD"],
            guidelines=title,
            state=tmp_path,
        )
    )
    wiki.login()
    monkeypatch.setattr("mw_cli.cli.client", lambda: wiki)

    def invoke(*args, code=0):
        result = CliRunner().invoke(cli, list(args))
        assert result.exit_code == code, result.output
        return json.loads(result.stdout) if code == 0 else result

    def active():
        return [
            x
            for b in invoke("blocks", "--user", target, "--all")["batches"]
            for x in b.get("query", {}).get("blocks", [])
        ]

    block_id = None
    assert not active(), "Test IP already blocked; refusing to alter an existing block"
    try:
        wiki.mutate(
            {
                "action": "edit",
                "title": title,
                "text": "Use short, explicit test blocks only.",
                "createonly": True,
                "summary": "mw-cli admin test fixture",
            }
        )
        args = [
            "block",
            target,
            "--expiry",
            "2 minutes",
            "--reason",
            "mw-cli disposable test",
            "--page",
            title,
            "--allow-talk",
            "--anon-only",
        ]
        invoke(*args, code=4)
        invoke("guidelines", "read")
        invoke(*args, "--dry-run")
        assert not active()
        blocked = invoke(*args)["block"]
        block_id = int(blocked["id"])
        rows = active()
        assert any(int(r["id"]) == block_id for r in rows)
        assert blocked["partial"] and not blocked.get("autoblock")
        invoke("unblock", "--id", str(block_id), "--reason", "test cleanup")
        block_id = None
        assert not active()
        actor = wiki.request({"action": "query", "meta": "userinfo"})["query"]["userinfo"]["name"]
        contributions = invoke("contributions", actor, "--limit", "1", "--max-batches", "5", "--all")
        assert any(
            c["title"] == title
            for b in contributions["batches"]
            for c in b.get("query", {}).get("usercontribs", [])
        )
        logs = invoke("logs", "--type", "block", "--title", "User:" + target, "--all")
        actions = {e["action"] for b in logs["batches"] for e in b.get("query", {}).get("logevents", [])}
        assert {"block", "unblock"}.issubset(actions)
    finally:
        if block_id is not None:
            wiki.mutate({"action": "unblock", "id": block_id, "reason": "mw-cli test cleanup"})
        from mw_cli.errors import WikiError

        try:
            wiki.mutate({"action": "delete", "title": title, "reason": "mw-cli test cleanup"})
        except WikiError as exc:
            if exc.code != "missingtitle":
                raise
        wiki.close()

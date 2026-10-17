"""Opt-in, unique-page test: MW_LIVE_AGENT=1 plus MW_LIVE_API/USERNAME/PASSWORD.
Requires a TEST wiki and edit/create/delete grants. Cleans up only its own pages.
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
@pytest.mark.skipif(os.environ.get("MW_LIVE_AGENT") != "1", reason="Requires explicit test-wiki agent setup")
def test_live_agent_editing(monkeypatch, tmp_path):
    title = "MwCliAgentTest-" + uuid.uuid4().hex
    template, rules = "Template:" + title, title + "-Rules"
    wiki = Client(
        Config(
            os.environ["MW_LIVE_API"],
            os.environ["MW_LIVE_USERNAME"],
            os.environ["MW_LIVE_PASSWORD"],
            guidelines=rules,
            state=tmp_path,
        )
    )
    wiki.login()
    monkeypatch.setattr("mw_cli.cli.client", lambda: wiki)

    def invoke(*args, input=None, code=0):
        result = CliRunner().invoke(cli, list(args), input=input)
        assert result.exit_code == code, result.output
        return json.loads(result.stdout) if code == 0 else result

    try:
        for name, text in [
            (rules, "Inspect the diff before saving."),
            (template, "A small test template"),
            (title, "{{" + title + "}}\n== Test section ==\nOld α text\n"),
        ]:
            wiki.mutate(
                {
                    "action": "edit",
                    "title": name,
                    "text": text,
                    "createonly": True,
                    "summary": "mw-cli agent workflow integration",
                }
            )
        page = wiki.read(title)
        base = str(page["revid"])
        info = invoke("info", title)["query"]["pages"][0]
        assert info["lastrevid"] == page["revid"] and info["fullurl"] and "protection" in info
        sections = invoke("sections", title)["parse"]
        assert sections["revid"] == page["revid"] and sections["sections"][0]["index"] == "1"
        templates = invoke("templates", title, "--all")
        assert template in [
            t["title"]
            for b in templates["batches"]
            for p in b["query"]["pages"]
            for t in p.get("templates", [])
        ]
        embedded = invoke("embedded-in", template, "--all")
        assert title in [p["title"] for b in embedded["batches"] for p in b["query"]["embeddedin"]]
        preview = invoke(
            "preview",
            title,
            "--file",
            "-",
            "--base-revid",
            base,
            input=page["content"].replace("Old α", "New β"),
        )
        assert preview["changed"] and "+New β" in preview["diff"]
        args = [
            "replace",
            title,
            "--old",
            "Old α",
            "--new",
            "New β",
            "--base-revid",
            base,
            "--summary",
            "mw-cli exact replacement test",
        ]
        invoke(*args, "--dry-run", code=4)
        invoke("guidelines", "read")
        invoke(*args, "--dry-run")
        assert wiki.read(title)["revid"] == page["revid"]
        saved = invoke(*args)
        assert saved["result"]["edit"]["result"] == "Success"
        assert "New β" in wiki.read(title)["content"]
        invoke(*args, code=1)  # stale base is rejected
        history = invoke(
            "api",
            "query",
            "--param",
            "prop=revisions",
            "--param",
            "titles=" + title,
            "--param",
            "rvlimit=1",
            "--all",
        )
        assert history["complete"] and len(history["batches"]) >= 2
        assert invoke("file", "list", "--prefix", title, "--all")["complete"]
    finally:
        from mw_cli.errors import WikiError

        for name in (title, template, rules):
            try:
                wiki.mutate({"action": "delete", "title": name, "reason": "mw-cli agent test cleanup"})
            except WikiError as exc:
                if exc.code != "missingtitle":
                    raise
        wiki.close()

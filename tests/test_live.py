"""Opt-in: MW_LIVE_DISPOSABLE=1 MW_LIVE_API=https://.../api.php
MW_LIVE_USERNAME=User@Bot MW_LIVE_PASSWORD=... pytest tests/test_live.py -v

Creates, edits, moves and deletes a UUID-prefixed page. Requires delete/move grants.
Never point this suite at production. No user-specified titles are modified.
"""

import os
import uuid

import pytest

from mw_cli.client import Client
from mw_cli.config import Config


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("MW_LIVE_DISPOSABLE") != "1", reason="Requires an explicitly disposable wiki"
)
def test_live_page_lifecycle():
    c = Client(
        Config(os.environ["MW_LIVE_API"], os.environ["MW_LIVE_USERNAME"], os.environ["MW_LIVE_PASSWORD"])
    )
    title = "MwCliTest-" + uuid.uuid4().hex
    moved = title + "-Moved"
    c.login()
    try:
        c.mutate(
            {
                "action": "edit",
                "title": title,
                "text": "Test α",
                "createonly": True,
                "summary": "mw-cli disposable test",
            }
        )
        page = c.read(title)
        assert page["content"] == "Test α"
        c.mutate(
            {
                "action": "edit",
                "title": title,
                "text": "Updated",
                "baserevid": page["revid"],
                "summary": "test update",
            }
        )
        c.mutate({"action": "move", "from": title, "to": moved, "reason": "test move"})
        assert c.read(moved)["content"] == "Updated"
    finally:
        from mw_cli.errors import WikiError

        for name in (title, moved):
            try:
                c.mutate({"action": "delete", "title": name, "reason": "cleanup disposable test"})
            except WikiError as exc:
                if exc.code != "missingtitle":
                    raise
        c.close()


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("MW_LIVE_DISPOSABLE") != "1", reason="Requires an explicitly disposable wiki"
)
def test_live_file_roundtrip(tmp_path):
    import base64

    from mw_cli import files

    c = Client(
        Config(os.environ["MW_LIVE_API"], os.environ["MW_LIVE_USERNAME"], os.environ["MW_LIVE_PASSWORD"])
    )
    c.login()
    name = "MwCliTest-" + uuid.uuid4().hex + ".png"
    source = tmp_path / name
    # One pixel PNG; unique file names keep operations isolated.
    source.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
        )
    )
    uploaded = False
    try:
        files.upload(c, source, comment="mw-cli disposable file test", chunk_size=32, ignore_warnings=True)
        uploaded = True
        destination = tmp_path / "download.png"
        files.download(c, name, destination)
        assert destination.read_bytes() == source.read_bytes()
    finally:
        if uploaded:
            c.mutate({"action": "delete", "title": "File:" + name, "reason": "cleanup disposable test"})
        c.close()

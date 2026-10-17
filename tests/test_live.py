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
def test_live_page_lifecycle(monkeypatch):
    import json

    from click.testing import CliRunner

    from mw_cli.blame import trace
    from mw_cli.cli import cli

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
                "text": "Updated [[" + title + "-Missing]]",
                "baserevid": page["revid"],
                "summary": "test update",
            }
        )
        c.mutate({"action": "move", "from": title, "to": moved, "reason": "test move"})
        assert c.read(moved)["content"].startswith("Updated")
        first = trace(c, moved, "Test α", mode="first")
        assert first["complete"] and first["origin"]["revid"] == page["revid"]
        current = trace(c, moved, "Updated")
        assert current["complete"] and current["origin"]["parentid"] == page["revid"]
        assert current["origin"]["diff_url"].startswith("http")
        monkeypatch.setattr("mw_cli.cli.client", lambda: c)
        result = CliRunner().invoke(cli, ["audit", "redlinks", "--title", moved, "--all"])
        assert result.exit_code == 0, result.output
        assert title + "-Missing" in [p["title"] for p in json.loads(result.stdout)["items"]]
        for report in ("redlinks", "missing-files", "broken-redirects", "double-redirects", "orphaned-pages"):
            result = CliRunner().invoke(cli, ["audit", report])
            assert result.exit_code == 0, result.output
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
    import secrets
    import struct
    import zlib

    from mw_cli import files

    c = Client(
        Config(os.environ["MW_LIVE_API"], os.environ["MW_LIVE_USERNAME"], os.environ["MW_LIVE_PASSWORD"])
    )
    c.login()
    name = "MwCliTest-" + uuid.uuid4().hex + ".png"
    source = tmp_path / name

    # A unique valid PNG large enough for multiple default-minimum 1 KiB chunks.
    def png_chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    pixels = b"".join(b"\x00" + secrets.token_bytes(32 * 3) for _ in range(32))
    source.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(pixels))
        + png_chunk(b"IEND", b"")
    )
    uploaded = False
    try:
        files.upload(c, source, comment="mw-cli disposable file test", chunk_size=1024)
        uploaded = True
        destination = tmp_path / "download.png"
        files.download(c, name, destination)
        assert destination.read_bytes() == source.read_bytes()
    finally:
        if uploaded:
            c.mutate({"action": "delete", "title": "File:" + name, "reason": "cleanup disposable test"})
        c.close()

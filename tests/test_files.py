import hashlib
from unittest.mock import Mock

import pytest
import requests

from mw_cli import files
from mw_cli.client import Client
from mw_cli.config import Config
from mw_cli.errors import WikiError


def make_client(wiki):
    c = Client(Config(wiki.url, "Tester@CLI", "password"))
    c.login()
    return c


def test_binary_integrity_and_limits(wiki, tmp_path, monkeypatch):
    wiki.files["Data.bin"] = b"test"
    c = make_client(wiki)
    with pytest.raises(WikiError):
        files.download(c, "Data.bin", tmp_path / "x", max_bytes=2)
    metadata = files.info(c, "Data.bin")
    monkeypatch.setattr(files, "info", lambda *a: {**metadata, "sha1": "0" * 40})
    with pytest.raises(WikiError) as error:
        files.download(c, "Data.bin", tmp_path / "x")
    assert error.value.code == "integrity-error"
    assert not (tmp_path / "x").exists()
    assert not list(tmp_path.glob(".mw-download-*"))
    monkeypatch.setattr(files, "info", lambda *a: {**metadata, "size": 5})
    with pytest.raises(WikiError, match="size"):
        files.download(c, "Data.bin", tmp_path / "x")
    monkeypatch.setattr(files, "info", lambda *a: {**metadata, "size": None})
    # Metadata without size is accepted and bounded by streamed bytes.
    with pytest.raises(WikiError):
        files.download(c, "Data.bin", tmp_path / "x", max_bytes=2)
    c.close()


def test_external_origin_receives_no_wiki_cookies(wiki, tmp_path, monkeypatch):
    wiki.files["Data.bin"] = b"test"
    c = make_client(wiki)
    metadata = files.info(c, "Data.bin")
    # Different hostname, same local test server. Origin distinction matters.
    monkeypatch.setattr(
        files, "info", lambda *a: {**metadata, "url": metadata["url"].replace("127.0.0.1", "localhost")}
    )
    files.download(c, "Data.bin", tmp_path / "test.bin")
    assert "Cookie" not in wiki.downloads[-1]
    c.close()


def test_download_network_failure_and_status(tmp_path, monkeypatch):
    c = Client(Config("https://wiki.example/api.php"))
    monkeypatch.setattr(
        files, "info", lambda *a: {"title": "File:X", "url": "https://wiki.example/file", "size": 4}
    )
    monkeypatch.setattr(c.session, "get", Mock(side_effect=requests.Timeout()))
    with pytest.raises(WikiError) as error:
        files.download(c, "X", tmp_path / "x")
    assert error.value.code == "download-failed"
    response = Mock(status_code=403)
    monkeypatch.setattr(c.session, "get", Mock(return_value=response))
    with pytest.raises(WikiError, match="403"):
        files.download(c, "X", tmp_path / "x")
    response = Mock(status_code=302, headers={"Location": "/another"})
    monkeypatch.setattr(c.session, "get", Mock(return_value=response))
    with pytest.raises(WikiError) as error:
        files.download(c, "X", tmp_path / "x")
    assert error.value.code == "redirect-loop"
    response.headers = {"Location": "http://wiki.example/file"}
    with pytest.raises(WikiError, match="downgrade"):
        files.download(c, "X", tmp_path / "x")
    c.close()


def test_base36_checksum_and_redirect(tmp_path, monkeypatch):
    c = Client(Config("https://wiki.example/api.php"))
    digest = int(hashlib.sha1(b"test").hexdigest(), 16)
    alphabet, encoded = "0123456789abcdefghijklmnopqrstuvwxyz", ""
    while digest:
        digest, digit = divmod(digest, 36)
        encoded = alphabet[digit] + encoded
    monkeypatch.setattr(
        files,
        "info",
        lambda *a: {"title": "File:X", "url": "https://wiki.example/file", "size": 4, "sha1": encoded},
    )
    redirect = Mock(status_code=302, headers={"Location": "/actual"})
    body = Mock(status_code=200)
    body.iter_content.return_value = [b"te", b"st"]
    monkeypatch.setattr(c.session, "get", Mock(side_effect=[redirect, body]))
    assert files.download(c, "X", tmp_path / "x")["bytes"] == 4
    c.close()


@pytest.mark.parametrize(
    "stage,reply,code",
    [
        ("stash", {"result": "Warning", "warnings": {"duplicate": ["Other.bin"]}}, "upload-warning"),
        ("stash", {"result": "Continue", "offset": 999, "filekey": "x"}, "upload-offset"),
        ("commit", {"result": "Warning", "warnings": {"exists": "X.bin"}}, "upload-warning"),
    ],
)
def test_upload_warnings_and_offset(wiki, tmp_path, stage, reply, code):
    path = tmp_path / "X.bin"
    path.write_bytes(b"data")

    def handle(p, b, h):
        if p.get("action") == "upload" and (("stash" in p) == (stage == "stash")):
            return 200, {"upload": reply}, {}

    wiki.handler = handle
    c = make_client(wiki)
    with pytest.raises(WikiError) as error:
        files.upload(c, path)
    assert error.value.code == code
    assert "X.bin" not in wiki.files
    c.close()


def test_upload_validation_and_guidelines(wiki, tmp_path):
    c = make_client(wiki)
    path = tmp_path / "X.bin"
    path.write_bytes(b"")
    with pytest.raises(WikiError, match="empty"):
        files.upload(c, path)
    path.write_bytes(b"abc")
    with pytest.raises(WikiError, match="filename"):
        files.upload(c, path, filename="../bad")
    c.config.guidelines = "Project:Rules"
    c.config.state = tmp_path
    with pytest.raises(WikiError) as error:
        files.upload(c, path)
    assert error.value.code == "guidelines-required"
    assert not wiki.stashes
    c.close()

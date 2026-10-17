import json
from unittest.mock import Mock

import pytest
import requests

from mw_cli.client import Client
from mw_cli.config import Config, read_json, resolve, write_json
from mw_cli.errors import WikiError
from mw_cli.guidelines import guard, read_guidelines
from mw_cli.semantic import ask


def response(value, status=200, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = value if isinstance(value, bytes) else json.dumps(value).encode()
    result._content_consumed = True
    result.headers.update(headers or {})
    return result


def fake_client(*responses):
    session = Mock(headers={}, proxies={})
    session.request.side_effect = list(responses)
    client = Client(Config("https://wiki.example/api.php"), session=session, sleep=Mock())
    return client


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_read_retries_are_bounded(status):
    c = fake_client(
        response({}, status, {"Retry-After": "date-not-number"}),
        response({}, status),
        response({"query": {}}),
    )
    assert c.request({"action": "query"}) == {"query": {}}
    assert c.session.request.call_count == 3
    assert c.sleep.call_count == 2


def test_maxlag_read_retry_but_not_write():
    c = fake_client(response({"error": {"code": "maxlag"}}), response({"ok": True}))
    assert c.request({"action": "query"})["ok"]
    c = fake_client(response({"error": {"code": "maxlag"}}))
    with pytest.raises(WikiError, match="rejected"):
        c.request({"action": "edit"}, post=True)
    assert c.session.request.call_count == 1


@pytest.mark.parametrize(
    "result,code",
    [
        (response({}, 302), "http-error"),
        (response(b"html"), "invalid-response"),
        (response([]), "invalid-response"),
    ],
)
def test_bad_http_responses(result, code):
    c = fake_client(result)
    with pytest.raises(WikiError) as error:
        c.request({"action": "query"})
    assert error.value.code == code


@pytest.mark.parametrize("post,code", [(False, "network-error"), (True, "outcome-unknown")])
def test_network_error_does_not_retry_mutation(post, code):
    c = fake_client(requests.Timeout("secret request contents"))
    with pytest.raises(WikiError) as error:
        c.request({"action": "query"}, post=post)
    assert error.value.code == code
    assert "secret" not in str(error.value)
    assert c.session.request.call_count == 1


def test_login_rejection_and_anonymous_tokens():
    c = fake_client(
        response({"query": {"tokens": {"logintoken": "login"}}}), response({"login": {"result": "Failed"}})
    )
    c.config.username = "Test@Bot"
    c.config.password = "secret"
    with pytest.raises(WikiError) as error:
        c.login()
    assert error.value.code == "login-failed"
    with pytest.raises(WikiError):
        c.token()
    assert "secret" not in repr(c.config)
    c.close()
    c.session.close.assert_called_once()


def test_unconfirmed_edit_is_not_success():
    c = fake_client(
        response({"query": {"tokens": {"csrftoken": "token"}}}),
        response({"edit": {"result": "Failure", "captcha": {}}}),
    )
    c.logged_in = True
    with pytest.raises(WikiError) as error:
        c.mutate({"action": "edit"})
    assert error.value.code == "edit-not-saved"


@pytest.mark.parametrize(
    "page,code",
    [
        ({}, "missing-page"),
        ({"invalid": True}, "missing-page"),
        ({"pageid": 1, "title": "Secret", "revisions": []}, "hidden-content"),
    ],
)
def test_read_missing_hidden(page, code):
    c = fake_client(response({"query": {"pages": [page]}}))
    with pytest.raises(WikiError) as error:
        c.read("Secret")
    assert error.value.code == code


def test_pagination_preserves_full_continuation_and_limits():
    first = {"query": {"items": [1]}, "continue": {"continue": "-||", "plcontinue": "12|0|Last"}}
    c = fake_client(response(first), response({"query": {"items": [2]}}))
    out = c.collect({"action": "query"}, all=True)
    assert out["complete"] and len(out["batches"]) == 2
    params = c.session.request.call_args.kwargs["params"]
    assert params["plcontinue"] == "12|0|Last" and params["continue"] == "-||"
    c = fake_client(response(first))
    assert not c.collect({"action": "query"}, all=True, max_batches=1)["complete"]
    c = fake_client(response(first), response(first))
    with pytest.raises(WikiError) as error:
        c.collect({"action": "query"}, all=True)
    assert error.value.code == "pagination-loop"


def test_smw_pagination():
    c = fake_client(response({"query-continue-offset": 50}), response({"query": {"results": {}}}))
    assert ask(c, "[[Category:Servers]]|?Owner|limit=50|offset=0", all=True)["complete"]
    assert (
        c.session.request.call_args.kwargs["params"]["query"]
        == "[[Category:Servers]]|?Owner|limit=50|offset=50"
    )
    c = fake_client(response({"query-continue-offset": 50}))
    assert not ask(c, "[[+]]", all=True, max_batches=1)["complete"]
    c = fake_client(response({"query-continue-offset": 50}), response({"query-continue-offset": 50}))
    with pytest.raises(WikiError):
        ask(c, "[[+]]", all=True)


def test_guideline_receipt_scope_and_output_failure(tmp_path):
    c = fake_client()
    c.config.guidelines = "Project:Rules"
    c.config.state = tmp_path
    c.read = Mock(return_value={"title": "Project:Rules", "revid": 1, "content": "rules"})
    with pytest.raises(OSError):
        read_guidelines(c, Mock(side_effect=OSError("output closed")))
    assert not c.config.receipt_path().exists()
    read_guidelines(c, Mock())
    guard(c)
    c.config.username = "Another@Bot"
    with pytest.raises(WikiError):
        guard(c)
    c.config.username = None
    c.read.return_value["content"] = "same revision but changed content"
    with pytest.raises(WikiError):
        guard(c)


def test_json_atomic_storage(tmp_path):
    path = tmp_path / "nested/file.json"
    assert read_json(path, {"empty": True}) == {"empty": True}
    write_json(path, {"a": "α"})
    assert read_json(path) == {"a": "α"}
    assert not list(path.parent.glob(".mw-cli-*"))
    path.write_text("not json")
    with pytest.raises(WikiError):
        read_json(path)


def test_config_resolution(tmp_path, monkeypatch):
    for key in ("MW_API_URL", "MW_USERNAME", "MW_PROFILE", "MW_GUIDELINES", "MW_BOT_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / "config.json"
    monkeypatch.setenv("MW_CLI_CONFIG", str(path))
    with pytest.raises(WikiError):
        resolve({})
    write_json(
        path,
        {
            "profiles": {
                "default": {
                    "api": "https://wiki.example/api.php",
                    "username": "Me@Bot",
                    "password_env": "CUSTOM_PASS",
                    "guidelines": "Rules",
                }
            }
        },
    )
    with pytest.raises(WikiError):
        resolve({})
    monkeypatch.setenv("CUSTOM_PASS", "secret")
    c = resolve({})
    assert c.password == "secret" and c.guidelines == "Rules"
    with pytest.raises(WikiError):
        resolve({"api": "http://wiki.example/api.php"})
    assert resolve({"api": "http://wiki.example/api.php", "allow_http": True}).api.startswith("http:")
    with pytest.raises(WikiError):
        resolve({"api": "file:///tmp/x"})
    write_json(path, {"profiles": []})
    with pytest.raises(WikiError):
        resolve({})

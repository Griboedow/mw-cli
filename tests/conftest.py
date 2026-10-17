import hashlib
import json
import threading
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import pytest


@pytest.fixture
def wiki(monkeypatch, tmp_path):
    """A real HTTP server implementing the narrow contracts exercised by the CLI."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.setenv("MW_CLI_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("MW_CLI_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("MW_USERNAME", "Tester@CLI")
    monkeypatch.setenv("MW_BOT_PASSWORD", "not-a-real-secret")
    monkeypatch.delenv("MW_GUIDELINES", raising=False)
    monkeypatch.delenv("MW_PROFILE", raising=False)
    state = SimpleNamespace(
        calls=[],
        pages={"Guide": (10, "Original α text"), "Project:Rules": (2, "Use clear headings.")},
        files={},
        stashes={},
        handler=None,
        revision=10,
        downloads=[],
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request()

        def handle_request(self):
            parts = urlsplit(self.path)
            if parts.path.startswith("/files/"):
                from urllib.parse import unquote

                name = unquote(parts.path.removeprefix("/files/"))
                state.downloads.append(dict(self.headers))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(state.files[name])
                return
            params = dict(parse_qsl(parts.query, keep_blank_values=True))
            binary = {}
            if self.command == "POST":
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                ctype = self.headers.get("Content-Type", "")
                if "multipart/form-data" in ctype:
                    message = BytesParser(policy=default).parsebytes(
                        f"Content-Type: {ctype}\r\n\r\n".encode() + body
                    )
                    for part in message.iter_parts():
                        key = part.get_param("name", header="content-disposition")
                        data = part.get_payload(decode=True)
                        if part.get_filename():
                            binary[key] = data
                        else:
                            params[key] = data.decode()
                else:
                    params.update(parse_qsl(body.decode(), keep_blank_values=True))
            state.calls.append((self.command, params.copy(), binary, dict(self.headers)))
            status, headers = 200, {}
            if state.handler:
                override = state.handler(params, binary, self)
                if override is not None:
                    status, result, headers = override
                    return self.respond(status, result, headers)
            action = params.get("action")
            if action == "login":
                result = {"login": {"result": "Success"}}
                headers["Set-Cookie"] = "wiki_session=private; Path=/; HttpOnly"
            elif action == "query" and params.get("meta") == "tokens":
                kind = params.get("type", "csrf")
                result = {"query": {"tokens": {kind + "token": "token+\\"}}}
            elif action == "query" and params.get("prop") == "revisions|info":
                title = params.get("titles", "Guide")
                if title not in state.pages:
                    result = {"query": {"pages": [{"title": title, "missing": True}]}}
                else:
                    rev, text = state.pages[title]
                    result = {
                        "curtimestamp": "2026-01-01T00:00:00Z",
                        "query": {
                            "pages": [
                                {
                                    "pageid": 1,
                                    "title": title,
                                    "revisions": [
                                        {
                                            "revid": rev,
                                            "timestamp": "2026-01-01T00:00:00Z",
                                            "slots": {"main": {"content": text, "contentmodel": "wikitext"}},
                                        }
                                    ],
                                }
                            ]
                        },
                    }
            elif action == "query" and params.get("prop") == "imageinfo":
                name = params["titles"].removeprefix("File:")
                metadata = []
                if name in state.files:
                    data = state.files[name]
                    metadata = [
                        {
                            "url": state.url.replace("/api.php", "/files/") + name,
                            "size": len(data),
                            "sha1": hashlib.sha1(data).hexdigest(),
                            "mime": "application/octet-stream",
                        }
                    ]
                result = {"query": {"pages": [{"title": "File:" + name, "imageinfo": metadata}]}}
            elif action == "edit":
                title = params["title"]
                if "createonly" in params and title in state.pages:
                    result = {"error": {"code": "articleexists"}}
                elif "baserevid" in params and (
                    title not in state.pages or int(params["baserevid"]) != state.pages[title][0]
                ):
                    result = {"error": {"code": "editconflict"}}
                else:
                    original = state.pages.get(title, (0, ""))[1]
                    text = params.get(
                        "text", params.get("prependtext", "") + original + params.get("appendtext", "")
                    )
                    state.revision += 1
                    state.pages[title] = (state.revision, text)
                    result = {"edit": {"result": "Success", "newrevid": state.revision, "title": title}}
            elif action == "move":
                state.pages[params["to"]] = state.pages.pop(params["from"])
                result = {"move": {"from": params["from"], "to": params["to"]}}
            elif action == "delete":
                state.pages.pop(params["title"], None)
                result = {"delete": {"title": params["title"]}}
            elif action == "upload":
                key = params.get("filekey", f"stash-{len(state.stashes) + 1}")
                if "stash" in params:
                    state.stashes[key] = state.stashes.get(key, b"") + binary["chunk"]
                    offset = len(state.stashes[key])
                    result = {
                        "upload": {
                            "result": "Success" if offset == int(params["filesize"]) else "Continue",
                            "filekey": key,
                            "offset": offset,
                        }
                    }
                else:
                    state.files[params["filename"]] = state.stashes[key]
                    result = {"upload": {"result": "Success", "filename": params["filename"]}}
            elif action == "parse":
                result = {"parse": {"text": "<p>Preview</p>"}}
            else:
                result = {"query": {"ok": True}}
            self.respond(status, result, headers)

        def respond(self, status, result, headers):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(result if isinstance(result, bytes) else json.dumps(result).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_port}/api.php"
    monkeypatch.setenv("MW_API_URL", state.url)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state
    server.shutdown()
    server.server_close()
    thread.join()

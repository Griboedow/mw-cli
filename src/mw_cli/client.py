import json
import time

import requests

from . import __version__
from .errors import WikiError, require


class Client:
    def __init__(self, config, *, session=None, sleep=time.sleep):
        self.config = config
        self.session = session or requests.Session()
        # Never pick up unrelated ~/.netrc credentials or implicit HTTP Basic authentication.
        self.session.trust_env = False
        # Explicit proxy/CA configuration remains available; credentials are never inferred.
        import os

        self.session.verify = os.environ.get("REQUESTS_CA_BUNDLE") or True
        self.session.headers["User-Agent"] = f"mw-cli/{__version__} (https://github.com/Griboedow/mw-cli)"
        self.sleep = sleep
        self.logged_in = False

    def close(self):
        self.session.close()

    def request(self, params, *, post=False, files=None, retries=2):
        values = {"format": "json", "formatversion": 2, "maxlag": 5, **params}
        values = {
            k: "1" if v is True else str(v) for k, v in values.items() if v is not None and v is not False
        }
        try:
            response = self.session.request(
                "POST" if post else "GET",
                self.config.api,
                **({"data": values, "files": files} if post else {"params": values}),
                timeout=self.config.timeout,
                allow_redirects=False,
                proxies=requests.utils.get_environ_proxies(self.config.api),
            )
        except requests.RequestException as exc:
            raise WikiError(
                "outcome-unknown" if post else "network-error",
                "Request interrupted. Check wiki state before retrying; it may have completed."
                if post
                else "Network request failed or timed out.",
            ) from exc
        try:
            if not post and retries and response.status_code in (429, 502, 503, 504):
                try:
                    delay = min(5, max(0.1, float(response.headers.get("Retry-After", "1"))))
                except ValueError:
                    delay = 1
                self.sleep(delay)
                return self.request(params, retries=retries - 1)
            if not 200 <= response.status_code < 300:
                raise WikiError(
                    "http-error",
                    f"HTTP {response.status_code}; verify the API URL. Redirects are not followed.",
                    details={"status": response.status_code},
                )
            try:
                result = response.json()
            except ValueError as exc:
                raise WikiError("invalid-response", "API did not return JSON.") from exc
        finally:
            response.close()
        if not isinstance(result, dict):
            raise WikiError("invalid-response", "API returned a non-object response.")
        if "error" in result:
            code = result["error"].get("code", "api-error")
            if not post and retries and code == "maxlag":
                self.sleep(1)
                return self.request(params, retries=retries - 1)
            raise WikiError(code, "MediaWiki rejected the request.", details={"api_code": code})
        return result

    def login(self):
        if not self.config.username:
            return
        token = self.request({"action": "query", "meta": "tokens", "type": "login"})
        result = self.request(
            {
                "action": "login",
                "lgname": self.config.username,
                "lgpassword": self.config.password,
                "lgtoken": token["query"]["tokens"]["logintoken"],
            },
            post=True,
        )
        if result.get("login", {}).get("result") != "Success":
            raise WikiError("login-failed", "Bot Password login failed. Check User@Bot, password and grants.")
        self.logged_in = True

    def token(self, kind="csrf"):
        require(self.logged_in, "This operation requires Bot Password authentication.")
        result = self.request({"action": "query", "meta": "tokens", "type": kind, "assert": "user"})
        return result["query"]["tokens"][kind + "token"]

    def mutate(self, params, *, files=None):
        token = self.token("watch" if params["action"] == "watch" else "csrf")
        result = self.request({**params, "assert": "user", "token": token}, post=True, files=files)
        if "edit" in result and result["edit"].get("result") != "Success":
            raise WikiError(
                "edit-not-saved",
                "Edit not confirmed as saved (possibly moderation, CAPTCHA or an edit filter).",
                details={"result": result["edit"].get("result")},
            )
        return result

    def read(self, title, *, revision=None, redirects=False):
        result = self.request(
            {
                "action": "query",
                "prop": "revisions|info",
                **({"revids": revision} if revision else {"titles": title}),
                "redirects": redirects,
                "rvprop": "ids|timestamp|content|contentmodel",
                "rvslots": "main",
                "curtimestamp": True,
            }
        )
        pages = result.get("query", {}).get("pages", [])
        page = pages[0] if pages else {}
        if not page or "missing" in page or "invalid" in page:
            raise WikiError("missing-page", f"Page does not exist: {title}", exit_code=3)
        revisions = page.get("revisions", [])
        rev = revisions[0] if revisions else {}
        slot = rev.get("slots", {}).get("main", {})
        if "content" not in slot:
            raise WikiError("hidden-content", "Revision content is not readable.", exit_code=3)
        return {
            "title": page["title"],
            "pageid": page["pageid"],
            "revid": rev["revid"],
            "timestamp": rev["timestamp"],
            "starttimestamp": result.get("curtimestamp"),
            "contentmodel": slot.get("contentmodel", page.get("contentmodel")),
            "content": slot["content"],
            "redirects": result.get("query", {}).get("redirects", []),
        }

    def collect(self, params, *, all=False, max_batches=100):
        batches, seen, continuation = [], set(), {}
        while True:
            result = self.request({**params, **continuation})
            batches.append(result)
            continuation = result.get("continue")
            if not continuation or not all or len(batches) >= max_batches:
                return {"batches": batches, "complete": not bool(continuation), "continuation": continuation}
            key = json.dumps(continuation, sort_keys=True)
            if key in seen:
                raise WikiError("pagination-loop", "API repeated its continuation token.")
            seen.add(key)

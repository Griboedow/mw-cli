"""Bounded, sequential attribution of exact phrase presence in source revisions."""

import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .errors import WikiError, require


def revision_info(revision, url):
    result = {key: revision.get(key) for key in ("revid", "parentid", "user", "timestamp", "comment")}
    result["user_hidden"] = "userhidden" in revision
    result["comment_hidden"] = "commenthidden" in revision
    if url:
        parts = urlsplit(url)
        query = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("diff", "oldid")]
        query.extend([("diff", str(revision["revid"])), ("oldid", "prev")])
        result["diff_url"] = urlunsplit(parts._replace(query=urlencode(query), fragment=""))
    return result


def trace(wiki, title, text, *, mode="current", max_revisions=1000):
    """Find first appearance, or the start of the current uninterrupted presence."""
    require(bool(text), "--text cannot be empty.")
    anchor = wiki.read(title)
    result = {
        "title": anchor["title"],
        "pageid": anchor["pageid"],
        "anchor_revid": anchor["revid"],
        "text": text,
        "mode": mode,
        "match": "exact-source-phrase-presence",
        "anchor_occurrences": anchor["content"].count(text),
        "checked_revisions": 0,
        "complete": False,
        "origin": None,
        "candidate": None,
        "limitations": "Tracks phrase presence, not individual words or duplicate occurrences. "
        "Reintroductions in current mode belong to the restoring edit. No cross-page provenance.",
    }
    if mode == "current" and text not in anchor["content"]:
        return {**result, "complete": True, "status": "absent-at-anchor"}
    params = {
        "action": "query",
        "prop": "revisions|info",
        "inprop": "url",
        "pageids": anchor["pageid"],
        "rvprop": "ids|timestamp|user|comment|content",
        "rvslots": "main",
        "rvdir": "older" if mode == "current" else "newer",
        "rvstartid" if mode == "current" else "rvendid": anchor["revid"],
    }
    continuation, seen = {}, set()
    expected = anchor["revid"] if mode == "current" else 0
    started = mode == "first"
    for _ in range(max_revisions):
        if result["checked_revisions"] >= max_revisions:
            break
        response = wiki.request(
            {**params, **continuation, "rvlimit": min(50, max_revisions - result["checked_revisions"])}
        )
        pages = response.get("query", {}).get("pages", [])
        page = next((p for p in pages if p.get("pageid") == anchor["pageid"]), {})
        revisions = page.get("revisions", [])
        for rev in revisions:
            # rvstartid/rvendid are timestamp boundaries; several edits can share a second.
            if mode == "current" and not started:
                if rev["revid"] != anchor["revid"]:
                    continue
                started = True
            if (mode == "current" and rev["revid"] != expected) or (
                mode == "first" and rev.get("parentid") != expected
            ):
                return {**result, "status": "history-gap"}
            content = rev.get("slots", {}).get("main", {}).get("content")
            if content is None:
                return {**result, "status": "hidden-content", "blocked_revid": rev["revid"]}
            result["checked_revisions"] += 1
            present = text in content
            if mode == "first" and present:
                return {
                    **result,
                    "complete": True,
                    "status": "found",
                    "origin": revision_info(rev, page.get("fullurl")),
                }
            if mode == "current":
                if not present:
                    return {
                        **result,
                        "complete": True,
                        "status": "found",
                        "origin": result["candidate"],
                        "boundary_revid": rev["revid"],
                    }
                result["candidate"] = revision_info(rev, page.get("fullurl"))
                if rev.get("parentid") == 0:
                    return {**result, "complete": True, "status": "found", "origin": result["candidate"]}
                expected = rev.get("parentid")
            else:
                expected = rev["revid"]
                if expected == anchor["revid"]:
                    return {**result, "complete": True, "status": "not-found"}
        continuation = response.get("continue")
        if not continuation:
            return {**result, "status": "history-gap"}
        key = json.dumps(continuation, sort_keys=True)
        if key in seen:
            raise WikiError("pagination-loop", "API repeated its continuation token.")
        seen.add(key)
    return {**result, "status": "limit-reached"}

"""Read-only review material for an explicitly selected revision."""

from .errors import WikiError


def inspect_revision(wiki, revision):
    response = wiki.request(
        {
            "action": "query",
            "prop": "revisions|info",
            "revids": revision,
            "inprop": "url",
            "rvslots": "main",
            "rvprop": "ids|timestamp|user|comment|flags|tags|content",
        }
    )
    pages = response.get("query", {}).get("pages", [])
    for page in pages:
        for rev in page.get("revisions", []):
            if rev["revid"] == revision:
                content = rev.get("slots", {}).get("main", {}).get("content")
                if content is None:
                    raise WikiError("hidden-content", "Revision source is not readable.", exit_code=3)
                parent = rev["parentid"]
                diff = (
                    wiki.request(
                        {"action": "compare", "fromrev": parent, "torev": revision, "prop": "diff|ids|title"}
                    )
                    if parent
                    else None
                )
                return {
                    "title": page["title"],
                    "pageid": page["pageid"],
                    "revision": rev,
                    "diff": diff,
                    "creation": parent == 0,
                    "note": "Read-only review; no patrol mark was applied. For creation inspect source.",
                }
    raise WikiError("missing-revision", "Revision does not exist or is not accessible.", exit_code=3)

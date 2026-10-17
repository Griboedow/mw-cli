"""Exact source edits and local previews; no template parsing or fuzzy matching."""

from difflib import unified_diff

from .errors import WikiError, require
from .guidelines import guard


def check_base(page, revision):
    if revision is not None and page["revid"] != revision:
        raise WikiError(
            "editconflict",
            "Page changed since the supplied base revision; read it again.",
            details={"expected": revision, "actual": page["revid"]},
        )


def preview(page, text):
    lines = unified_diff(
        page["content"].splitlines(keepends=True),
        text.splitlines(keepends=True),
        fromfile=f"{page['title']}@{page['revid']}",
        tofile=page["title"] + "@proposed",
    )
    diff = "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines
    )
    return {
        "title": page["title"],
        "base_revid": page["revid"],
        "changed": text != page["content"],
        "diff": diff,
        "note": "Local source diff; no server pre-save transformation, rendering or write.",
    }


def replace(wiki, title, old, new, *, base_revid, expected_count, summary, dry_run=False):
    require(bool(old), "--old cannot be empty.")
    require(wiki.logged_in, "Mutations require Bot Password authentication.")
    guard(wiki)
    page = wiki.read(title)
    check_base(page, base_revid)
    count = page["content"].count(old)
    if count != expected_count:
        raise WikiError(
            "match-count-mismatch",
            "Exact non-overlapping match count differs from --count.",
            details={"expected": expected_count, "actual": count},
        )
    text = page["content"].replace(old, new)
    result = {**preview(page, text), "matches": count, "dry_run": dry_run}
    if dry_run or not result["changed"]:
        return result
    result["result"] = wiki.mutate(
        {
            "action": "edit",
            "pageid": page["pageid"],
            "baserevid": base_revid,
            "nocreate": True,
            "text": text,
            "summary": summary,
        }
    )
    return result

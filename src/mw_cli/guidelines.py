import hashlib

from .config import read_json, write_json
from .errors import WikiError


def identity(page):
    return {
        "title": page["title"],
        "revid": page["revid"],
        "sha256": hashlib.sha256(page["content"].encode()).hexdigest(),
    }


def read_guidelines(client, emit):
    if not client.config.guidelines:
        raise WikiError("no-guidelines", "Configure --guidelines or a profile guidelines page first.")
    page = client.read(client.config.guidelines, redirects=True)
    emit(
        {
            **page,
            "instructions": "Read and follow this page before mutations. Read referenced rules separately.",
        }
    )
    write_json(client.config.receipt_path(), identity(page))


def guard(client):
    if not client.config.guidelines:
        return
    page = client.read(client.config.guidelines, redirects=True)
    receipt = read_json(client.config.receipt_path())
    if receipt != identity(page):
        raise WikiError(
            "guidelines-required",
            "Run mw-cli guidelines read with the same profile, read its output, then retry. Guidelines are unread or changed.",
            details={"title": page["title"], "revid": page["revid"]},
            exit_code=4,
        )

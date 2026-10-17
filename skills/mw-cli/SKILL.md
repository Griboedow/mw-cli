---
name: mw-cli
description: Read, search and edit MediaWiki pages or transfer wiki files using the Python mw-cli and Bot Passwords.
---

Use `mw-cli --help`, then the relevant command's `--help`; these are the operational manual.
Use the user's profile/endpoint and environment-supplied Bot Password; never put secrets in arguments.
Discover extensions and rights with `capabilities`. If guidelines are configured, run `guidelines read`
and read the returned rules before mutations. Read linked rules when relevant.
For edits retain the original read's `revid` and use `write --base-revid`; use `--create-only` for new pages.
Inspect `complete`/`continuation` on collections and verify actual save results.
After `outcome-unknown`, inspect wiki history/state before retrying.

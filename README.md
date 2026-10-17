# mw-cli

A Python CLI for MediaWiki, designed for both humans and agents working with local files.
The command help is the operational manual. The bundled agent skills are deliberately small.

## Install

Python 3.10 or newer, on Windows, macOS or Linux:

```sh
uv tool install git+https://github.com/Griboedow/mw-cli.git
# or, inside your Python environment:
pip install git+https://github.com/Griboedow/mw-cli.git
mw-cli --help
```

The Python distribution is named `griboedow-mw-cli`; the executable is `mw-cli`.
This project is not yet published to PyPI.

## Connect

Create a Bot Password on your wiki's `Special:BotPasswords`, granting only the
operations you need (edit, create, upload/reupload, move, delete, etc.). The account
must also have the corresponding wiki rights; a Bot Password cannot elevate them.

```sh
mw-cli config set work --api https://wiki.example/w/api.php \
  --username 'YourName@Agent' --guidelines 'Project:Editing guidelines'
export MW_BOT_PASSWORD='your-bot-password'
mw-cli --profile work whoami
mw-cli --profile work capabilities
mw-cli --profile work guidelines read
```

In PowerShell set `$env:MW_BOT_PASSWORD = 'your-bot-password'`. Prefer injecting the
environment variable from your secret manager instead of retaining it in shell history.
Never put a password in a command argument, repository, skill or profile JSON.
Profiles can select another secret variable with `config set --password-env NAME`.

Global options precede the command. Environment alternatives: `MW_API_URL`,
`MW_USERNAME`, `MW_PROFILE`, `MW_GUIDELINES`, `MW_CLI_CONFIG`, `MW_CLI_STATE`.
Config defaults to `~/.config/mw-cli/config.json`. Sessions/cookies are not persisted.
HTTPS is required for credentials except localhost, unless `--allow-http` is explicit.
Corporate certificates use `REQUESTS_CA_BUNDLE`; proxies use `HTTP_PROXY`,
`HTTPS_PROXY` and `NO_PROXY`. TLS verification is never disabled by a CLI flag.

## Work with pages and files

```sh
mw-cli --profile work read 'Guide' --output guide.wiki
# Edit guide.wiki locally. Keep the revid returned by that read, e.g. 123.
mw-cli --profile work write 'Guide' --file guide.wiki --base-revid 123 --summary 'Update procedure'
mw-cli --profile work write 'New guide' --file guide.wiki --create-only --summary 'Add guide'
mw-cli --profile work search 'backup restore' --all
mw-cli --profile work move 'Old title' 'New title' --reason 'Rename guide'
mw-cli --profile work file download 'File:Architecture.svg' --output architecture.svg
mw-cli --profile work file upload architecture.svg --comment 'Add architecture diagram'
mw-cli --profile work smw ask '[[Category:Servers]]|?Owner|limit=50' --all
```

Other commands: `pages`, `history`, `links`, `backlinks`, `categories`,
`category-members`, `recent-changes`, `delete`, `undelete`, `undo`, `protect`,
`watch`, `purge`, `parse`, `diff`, `smw askargs`, `smw browse`, `smw info`, `api`.
Use `COMMAND --help` for examples and constraints. `api paraminfo` discovers
extension-specific modules. SMW writes use the normal wikitext edit workflow.

## Phrase attribution and diagnostics

```sh
mw-cli blame 'Guide' --text 'some words'
mw-cli blame 'Guide' --text 'some words' --mode first --max-revisions 5000
mw-cli audit redlinks --title 'Guide' --all
mw-cli audit redlinks --all
mw-cli audit missing-files --all
mw-cli audit broken-redirects --all
mw-cli audit double-redirects --all
mw-cli audit orphaned-pages --all
```

`blame` returns author, timestamp, revision, edit summary and a diff URL. The default
`current` mode finds the beginning of uninterrupted **exact phrase presence** ending
at the pinned current revision. A restoring edit starts a new episode. `--mode first`
finds the earliest appearance, including phrases no longer present. Matching is
case-sensitive against main-slot source, not rendered/transcluded text. This is not
per-word attribution or tracking a particular duplicate/copy across pages.

Search is sequential and bounded (1,000 revisions by default), because text may
disappear and reappear. Always inspect `complete` and `status`: hidden content,
history gaps and limits leave `origin=null`; `candidate` is only a provisional
boundary. Exit 0 means the search ran, not that an origin was proven. Raise the limit
and rerun to search deeper. Hidden authors/summaries remain null and explicitly flagged.

`audit redlinks --title` filters missing outgoing link targets into `items`, keeping
raw API `batches` and continuation metadata. Wiki-wide reports use MediaWiki's
QueryPage caches and can be stale/capped even when API pagination is complete. They
do not check external URLs or change the wiki. Use `backlinks TITLE` to find referring
pages. Diagnostics and blame require only read permissions; no new runtime dependency.

API contracts: [revisions](https://www.mediawiki.org/wiki/API:Revisions),
[link targets](https://www.mediawiki.org/wiki/API:Links),
[QueryPage reports](https://www.mediawiki.org/wiki/API:Querypage).

## Agent contract

- JSON on stdout by default; `read --raw` emits source and `parse --raw` emits HTML.
- JSON API/network/local errors on stderr. Click usage errors are human-readable.
- Exit codes: **0** success, **1** operation failure, **2** invalid input,
  **3** missing/hidden content, **4** guidelines required.
- Collections return `batches`, `complete`, `continuation`. `--all` follows pagination
  up to `--max-batches`, never silently discarding the indication of partial results.
- Writes require `--base-revid` from the source read, or `--create-only`.
  Edit conflicts are surfaced, not automatically overwritten.
- Mutations support `--dry-run`, including guidelines/authentication checks.
- Mutation network failures are **outcome-unknown**. Inspect state/history before
  retrying. Reads retry bounded transient HTTP/maxlag errors; writes do not.
- Uploads are chunked (default 5 MiB), with a separate final commit. Warnings are
  not silently ignored. Existing files need `--overwrite`; warnings may additionally
  require deliberate `--ignore-warnings`. Stashes may remain until MediaWiki expires them.
- Downloads stream to a temporary file, verify size/SHA-1 and atomically publish;
  cookies never go to a different origin. External storage with separate authentication
  is not currently supported.

## Mandatory guidelines

`--guidelines 'Project:Rules'`, `MW_GUIDELINES`, or the profile setting specifies the
rules page. `guidelines read` prints its complete source and stores a receipt bound
to endpoint, login, title, revision and content hash. Every mutation re-reads the
current rules page and requires a matching receipt. Changed, missing or inaccessible
rules block the operation, including raw API mutations and uploads.

This is a workflow guard, not access control: it verifies delivery of the text, not
comprehension. Users controlling local config can change it. Enforcement applies
when guidelines are configured. There is no `--skip-guidelines` switch. Keep mandatory
rules directly on that page; receipts do not track transcluded/linked dependencies.
There is an unavoidable interval between checking rules and the server write.

## Skills

Separate folders in `skills/`: `mw-cli`, `mw-mermaid`, `mw-drawio`. Copy the desired
folder(s) into your agent's skill directory. They are also included inside the Python
wheel under `mw_cli/skills`. Core help contains the command documentation; the diagram
skills explain extension-specific authoring and do not assume a universal diagram tag.

## Development

```sh
python -m venv .venv
# activate .venv using your shell's normal activation command
pip install -e '.[test]'
pytest --cov=mw_cli --cov-report=term-missing
ruff check src tests
python -m build
```

The offline suite exercises HTTP contracts against an isolated local server plus
failure-path tests. Opt-in live tests require a **disposable** MediaWiki and a Bot
Password; see `tests/test_live.py`. Unit coverage is not a promise that every extension
or MediaWiki release has been tested. Baseline: MediaWiki 1.39+ Action API, main slot;
extension/version compatibility depends on the target installation.

CI runs Python 3.10/3.12/3.14 on Windows/Linux/macOS and provisions disposable
MediaWiki 1.43.1 and 1.45.0 with SQLite for real Bot Password/page/file tests.
`python tests/run_disposable_wiki.py 1.43.1` reproduces the live setup with Python
3.12+ and a PHP CLI with sqlite3, intl, mbstring, XML and fileinfo available.
Semantic MediaWiki is covered by API-contract tests, not this core-only live matrix.

API references: [Action API](https://www.mediawiki.org/wiki/API:Main_page),
[Bot Password login](https://www.mediawiki.org/wiki/API:Login),
[edit](https://www.mediawiki.org/wiki/API:Edit),
[upload](https://www.mediawiki.org/wiki/API:Upload),
[SMW API](https://github.com/SemanticMediaWiki/SemanticMediaWiki/blob/master/docs/technical/api.md).

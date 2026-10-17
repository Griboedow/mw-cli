"""Self-documenting command tree. Stdout is data; stderr is structured errors."""

import json
from pathlib import Path

import click

from . import __version__, admin, editing, files, semantic
from . import blame as attribution
from . import patrol as patrolling
from .client import Client
from .config import config_path, read_json, resolve, write_json
from .errors import WikiError, require
from .guidelines import guard, read_guidelines


def emit(value):
    click.echo(json.dumps(value, ensure_ascii=False, indent=2))


class RootGroup(click.Group):
    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except WikiError as exc:
            emit_error(exc.code, str(exc), exc.details)
            ctx.exit(exc.exit_code)
        except (OSError, ValueError) as exc:
            emit_error("local-error", "Local file or data operation failed.", {"type": type(exc).__name__})
            ctx.exit(1)


def emit_error(code, message, details=None):
    click.echo(
        json.dumps({"error": {"code": code, "message": message, "details": details}}, ensure_ascii=False),
        err=True,
    )


@click.group(cls=RootGroup, context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__)
@click.option("--api", help="Exact Action API URL, e.g. https://wiki.example/w/api.php. Env: MW_API_URL.")
@click.option("--profile", help="Saved profile name (default: default). Env: MW_PROFILE.")
@click.option("--config", type=click.Path(path_type=Path), help="Config JSON path. Env: MW_CLI_CONFIG.")
@click.option("--username", help="Bot Password login, User@Bot. Env: MW_USERNAME. Password: MW_BOT_PASSWORD.")
@click.option(
    "--guidelines",
    help="Wiki page of mandatory editing rules. Run guidelines read before ANY mutation. Env: MW_GUIDELINES.",
)
@click.option(
    "--timeout",
    type=click.FloatRange(min=0.1),
    default=30,
    show_default=True,
    help="Per-request connect/read timeout, seconds.",
)
@click.option(
    "--allow-http", is_flag=True, help="Explicitly allow Bot Passwords over non-local HTTP (intranet only)."
)
@click.pass_context
def cli(ctx, **options):
    """Read and edit MediaWiki from a computer or an agent. Python 3.10+.

    Every command has --help with parameters and examples. JSON is the default
    stdout format; read --raw and parse --raw emit text. No interactive prompts.
    Put global options BEFORE the command. Passwords are environment variables
    only; sessions/cookies are memory-only, with one login per invocation.

    \b
    Start:
      mw-cli config set work --api https://wiki.example/w/api.php --username User@Bot --guidelines Project:Editing
      mw-cli --profile work guidelines read
      mw-cli --profile work read 'Main Page'
      mw-cli --profile work write 'Main Page' --file page.wiki --base-revid 123 --summary 'Update guide'

    \b
    Agent workflow: capabilities -> guidelines read -> read -> write with revid.
    Use --dry-run on mutations to inspect the request without writing.
    Collections return batches, complete, continuation; --all follows pagination
    up to --max-batches. Never silently assume a partial result is complete.

    \b
    Exit codes: 0 success; 1 API/network/local error; 2 invalid usage;
    3 missing/hidden page or file; 4 guidelines unread/changed.
    Mutation transport failures have outcome-unknown: inspect the wiki before
    retrying. No blind mutation retries or automatic edit-conflict resolution.
    """
    ctx.ensure_object(dict)
    ctx.obj["options"] = options


def client():
    ctx = click.get_current_context().find_root()
    if "client" not in ctx.obj:
        wiki = Client(resolve(ctx.obj["options"]))
        ctx.call_on_close(wiki.close)
        wiki.login()
        ctx.obj["client"] = wiki
    return ctx.obj["client"]


def mutation(params, dry_run):
    wiki = client()
    require(wiki.logged_in, "Mutations require Bot Password authentication.")
    guard(wiki)
    emit({"dry_run": True, "parameters": params} if dry_run else wiki.mutate(params))


def dry_option(fn):
    return click.option(
        "--dry-run",
        is_flag=True,
        help="Check authentication/guidelines and show parameters; do not mutate the wiki.",
    )(fn)


def paging(fn):
    fn = click.option(
        "--max-batches",
        type=click.IntRange(1, 10000),
        default=100,
        show_default=True,
        help="Hard stop for --all; complete=false means more results remain.",
    )(fn)
    fn = click.option("--all", "all_", is_flag=True, help="Follow continuation tokens up to --max-batches.")(
        fn
    )
    fn = click.option(
        "--limit", type=click.IntRange(1, 500), default=50, show_default=True, help="Items per API batch."
    )(fn)
    return fn


@cli.group()
def config():
    """Save named wiki profiles. Passwords and session cookies are never saved."""


@config.command("set")
@click.argument("name")
@click.option("--api", required=True, help="Exact Action API endpoint URL.")
@click.option("--username", help="User@Bot from Special:BotPasswords; omit for anonymous reads.")
@click.option(
    "--password-env",
    default="MW_BOT_PASSWORD",
    show_default=True,
    help="Environment variable containing the Bot Password.",
)
@click.option(
    "--guidelines", help="Mandatory rules page, e.g. Project:Editing. Omission preserves existing rules."
)
@click.pass_context
def config_set(ctx, name, api, username, password_env, guidelines):
    """Create/update NAME; no network request. Existing unspecified fields are preserved.

    Example: mw-cli config set work --api https://wiki.example/w/api.php --username User@Agent --guidelines Project:Rules
    """
    from urllib.parse import urlsplit

    url = urlsplit(api)
    require(
        url.scheme in ("https", "http")
        and url.hostname
        and not url.username
        and not url.password
        and not url.query
        and not url.fragment,
        "Provide a clean HTTP(S) API endpoint URL.",
    )
    require(password_env.isidentifier(), "--password-env must be an environment variable name.")
    path = ctx.find_root().obj["options"]["config"] or config_path()
    data = read_json(path, {"profiles": {}})
    require(
        isinstance(data, dict) and isinstance(data.get("profiles", {}), dict),
        "Invalid profiles configuration.",
    )
    profile = data.setdefault("profiles", {}).setdefault(name, {})
    profile.update({"api": api, "password_env": password_env})
    if username is not None:
        profile["username"] = username
    if guidelines is not None:
        require(guidelines.strip(), "Guidelines title cannot be empty.")
        profile["guidelines"] = guidelines
    write_json(path, data)
    emit({"profile": name, "settings": profile, "path": str(path)})


@config.command("show")
@click.pass_context
def config_show(ctx):
    """Show configured endpoints, usernames and password VARIABLE NAMES (no secrets)."""
    path = ctx.find_root().obj["options"]["config"] or config_path()
    data = read_json(path, {"profiles": {}})
    emit(
        {
            "profiles": {
                name: {
                    k: v for k, v in profile.items() if k in ("api", "username", "password_env", "guidelines")
                }
                for name, profile in data.get("profiles", {}).items()
            }
        }
    )


@cli.group()
def guidelines():
    """Read configured editing rules; writes require a receipt for the CURRENT revision.

    Receipt scope: API URL + username + guidelines title. Stored under MW_CLI_STATE
    or ~/.local/state/mw-cli. This verifies that text was delivered, not human/AI
    comprehension. Linked/transcluded rule pages must be read separately.
    A missing/unreadable rules page blocks mutation. No automatic bypass flag.
    """


@guidelines.command("read")
def guidelines_read():
    """Print the complete rules wikitext and record its revision after output succeeds."""
    read_guidelines(client(), emit)


@cli.command()
def capabilities():
    """Discover MediaWiki version, extensions, namespaces and authenticated user rights.

    Check for Semantic MediaWiki, Mermaid, DrawioEditor or Flex Diagrams before
    using extension-specific commands or skills. Extension names vary by version.
    """
    emit(
        client().request(
            {
                "action": "query",
                "meta": "siteinfo|userinfo",
                "siprop": "general|extensions|namespaces|namespacealiases",
                "uiprop": "rights|groups",
            }
        )
    )


@cli.command()
def whoami():
    """Verify Bot Password login and inspect effective groups/rights (anonymous if unset)."""
    emit(client().request({"action": "query", "meta": "userinfo", "uiprop": "rights|groups"}))


@cli.command()
@click.argument("title")
@click.option(
    "--revision", type=click.IntRange(min=1), help="Read this revision ID instead of the current title."
)
@click.option("--redirects", is_flag=True, help="Follow redirects (default reads the redirect itself).")
@click.option(
    "--raw", is_flag=True, help="Emit only UTF-8 source text; JSON normally includes revid for editing."
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    help="Save source to a NEW local file; JSON metadata remains on stdout.",
)
def read(title, revision, redirects, raw, output):
    """Read page source and revision metadata. Supports any readable main-slot content model.

    Example: mw-cli read 'Project:Guide' --output guide.wiki
    Keep the returned revid for write --base-revid. Never derive it from a later read.
    """
    page = client().read(title, revision=revision, redirects=redirects)
    if output:
        with output.open("x", encoding="utf-8", newline="") as stream:
            stream.write(page["content"])
        emit({**{k: v for k, v in page.items() if k != "content"}, "path": str(output.resolve())})
    elif raw:
        click.echo(page["content"], nl=False)
    else:
        emit(page)


@cli.command()
@click.argument("title")
@click.option(
    "--file",
    "source",
    type=click.File("r", encoding="utf-8"),
    required=True,
    help="UTF-8 source file; '-' reads stdin.",
)
@click.option(
    "--base-revid",
    type=click.IntRange(min=1),
    help="Revision returned by the read used to prepare this edit.",
)
@click.option(
    "--create-only",
    is_flag=True,
    help="Create a new page; fail if it already exists. Exclusive with --base-revid.",
)
@click.option("--summary", required=True, help="Edit summary explaining the change.")
@click.option(
    "--mode", type=click.Choice(["replace", "append", "prepend"]), default="replace", show_default=True
)
@click.option("--section", help="Section index, or 'new' to add a section. Not allowed with append/prepend.")
@click.option("--section-title", help="Heading for --section new.")
@click.option(
    "--content-model", help="Content model for new pages (wikitext, json, Scribunto, extension model)."
)
@click.option("--minor", is_flag=True, help="Mark as a minor edit.")
@click.option("--bot", is_flag=True, help="Mark as a bot edit (requires bot right).")
@dry_option
def write(
    title,
    source,
    base_revid,
    create_only,
    summary,
    mode,
    section,
    section_title,
    content_model,
    minor,
    bot,
    dry_run,
):
    """Create or edit a page, rejecting concurrent changes. No implicit overwrite.

    Exactly one of --base-revid and --create-only is required. Conflicts must be
    resolved against a fresh read; the CLI never retries a conflicting edit.
    SMW annotations are written as ordinary wikitext; indexing can be asynchronous.

    \b
    mw-cli write 'Guide' --file guide.wiki --base-revid 123 --summary 'Fix steps'
    mw-cli write 'New guide' --file guide.wiki --create-only --summary 'Create guide'
    mw-cli write 'Talk:Guide' --file note.wiki --base-revid 124 --section new --section-title 'Question' --summary 'Ask question'
    """
    require(bool(base_revid) != create_only, "Specify exactly one of --base-revid or --create-only.")
    require(not section or mode == "replace", "--section requires --mode replace.")
    require(not section_title or section == "new", "--section-title requires --section new.")
    text = source.read()
    params = {
        "action": "edit",
        "title": title,
        "summary": summary,
        "baserevid": base_revid,
        "createonly": create_only,
        "nocreate": bool(base_revid),
        "section": section,
        "sectiontitle": section_title,
        "contentmodel": content_model,
        "minor": minor,
        "bot": bot,
        {"replace": "text", "append": "appendtext", "prepend": "prependtext"}[mode]: text,
    }
    mutation(params, dry_run)


@cli.command("preview")
@click.argument("title")
@click.option(
    "--file",
    "source",
    type=click.File("r", encoding="utf-8"),
    required=True,
    help="Proposed complete UTF-8 source; '-' reads stdin.",
)
@click.option("--base-revid", type=click.IntRange(min=1), help="Reject if the current revision differs.")
def preview_edit(title, source, base_revid):
    """Show a local unified source diff against an existing page, without writing.

    Example: mw-cli preview Guide --file guide.wiki --base-revid 123
    Returns base_revid, changed, diff. Does not require editing rights/guidelines.
    This is not parser validation: use parse --file for a server-rendered preview.
    Save with write --base-revid using the revision inspected here.
    """
    page = client().read(title)
    editing.check_base(page, base_revid)
    emit(editing.preview(page, source.read()))


@cli.command("replace")
@click.argument("title")
@click.option("--old", required=True, help="Exact case-sensitive source fragment, not a regex.")
@click.option("--new", required=True, help="Replacement text; an empty string deletes the fragment.")
@click.option(
    "--count",
    "expected_count",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help="Expected number of non-overlapping occurrences; all must match, then all are replaced.",
)
@click.option(
    "--base-revid",
    type=click.IntRange(min=1),
    required=True,
    help="Revision you read before deciding to replace this fragment.",
)
@click.option("--summary", required=True, help="Edit summary.")
@dry_option
def replace_text(title, old, new, expected_count, base_revid, summary, dry_run):
    """Replace an exact fragment with revision and match-count checks; returns a diff.

    Example: mw-cli replace Guide --old 'old name' --new 'new name' --base-revid 123 --summary 'Rename' --dry-run
    Reads the current source, requires the supplied base revision and exact --count,
    then saves with server-side conflict detection. Ambiguous matches are errors.
    Guidelines/authentication apply even in dry-run. Identical output makes no edit.
    No regex, template-aware rewriting, implicit redirects or conflict retries.
    """
    require(bool(old), "--old cannot be empty.")
    emit(
        editing.replace(
            client(),
            title,
            old,
            new,
            base_revid=base_revid,
            expected_count=expected_count,
            summary=summary,
            dry_run=dry_run,
        )
    )


@cli.command("info")
@click.argument("title")
def page_info(title):
    """Inspect page existence, latest revision, content model, URL and protection.

    Missing pages remain marked missing in JSON, useful for existence checks.
    Does not follow redirects. Example: mw-cli info 'Template:Infobox'
    """
    emit(
        client().request(
            {"action": "query", "prop": "info", "titles": title, "inprop": "url|protection|talkid|subjectid"}
        )
    )


@cli.command("sections")
@click.argument("title")
def sections(title):
    """Get parser section indices/headings/anchors for write --section.

    Indices can change after edits; retain the source base_revid when writing.
    Transcluded sections may belong to a different page: inspect fromtitle/index.
    """
    emit(client().request({"action": "parse", "page": title, "prop": "sections|revid"}))


@cli.command()
@click.argument("query")
@click.option("--namespace", default="0", show_default=True, help="Namespace IDs separated by '|'.")
@paging
def search(query, namespace, limit, all_, max_batches):
    """Full-text search (syntax depends on the wiki search backend).

    Example: mw-cli search 'backup restore' --namespace '0|4' --all
    """
    emit(
        client().collect(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srnamespace": namespace,
                "srlimit": limit,
                "srprop": "snippet|timestamp|size",
            },
            all=all_,
            max_batches=max_batches,
        )
    )


@cli.command("pages")
@click.option("--prefix", default="", help="Title prefix without namespace.")
@click.option("--namespace", type=int, default=0, show_default=True, help="Namespace ID.")
@paging
def pages(prefix, namespace, limit, all_, max_batches):
    """List pages in title order. Example: mw-cli pages --prefix API --all"""
    emit(
        client().collect(
            {
                "action": "query",
                "list": "allpages",
                "apprefix": prefix,
                "apnamespace": namespace,
                "aplimit": limit,
            },
            all=all_,
            max_batches=max_batches,
        )
    )


@cli.command()
@click.argument("title")
@paging
def history(title, limit, all_, max_batches):
    """Read revision IDs, authors, timestamps and edit summaries, newest first."""
    emit(
        client().collect(
            {
                "action": "query",
                "prop": "revisions",
                "titles": title,
                "rvlimit": limit,
                "rvprop": "ids|timestamp|user|comment|size",
            },
            all=all_,
            max_batches=max_batches,
        )
    )


@cli.command()
@click.argument("title")
@click.option("--text", required=True, help="Exact case-sensitive phrase in source wikitext; quote it.")
@click.option(
    "--mode",
    type=click.Choice(["current", "first"]),
    default="current",
    show_default=True,
    help="Current uninterrupted presence, or earliest appearance in page history.",
)
@click.option(
    "--max-revisions",
    type=click.IntRange(1, 100000),
    default=1000,
    show_default=True,
    help="Maximum revisions inspected. A limit returns complete=false, origin=null.",
)
def blame(title, text, mode, max_revisions):
    """Find who introduced a phrase, when, and in which edit (including a diff URL).

    Example: mw-cli blame Guide --text 'some words' --mode first

    Searches exact MAIN-SLOT SOURCE, not rendered HTML or transcluded text. Pins
    the current revision, scans sequentially with pagination and stops at hidden
    content/history gaps. JSON status/complete must be checked even on exit 0.
    candidate is NOT a proven origin. Increase --max-revisions and rerun if limited.

    current finds the start of uninterrupted phrase presence ending at the pinned
    revision; an edit restoring removed text is its origin. first searches from
    page creation and also works for text no longer present. This is phrase-level
    presence, not per-word authorship: duplicates, moves within a page, spelling
    changes and cross-page copying cannot identify an individual occurrence.
    """
    require(bool(text), "--text cannot be empty.")
    emit(attribution.trace(client(), title, text, mode=mode, max_revisions=max_revisions))


@cli.group()
def audit():
    """Read-only wiki diagnostics. Each report has --help and bounded pagination.

    Wiki-wide reports use server QueryPage caches, which may be stale or capped.
    complete means API pagination exhausted, not a fresh exhaustive wiki scan.
    Nothing is fixed/deleted automatically. Use backlinks to locate referring pages.
    """


@audit.command("redlinks")
@click.option("--title", help="Inspect outgoing links on this page instead of wiki-wide Wantedpages.")
@paging
def redlinks(title, limit, all_, max_batches):
    """Find missing link targets; includes namespace/title and server report metadata.

    Examples: mw-cli audit redlinks --all
              mw-cli audit redlinks --title Guide --all

    With --title, returns items plus raw batches; items contain only missing
    targets. The source must exist. Without --title uses cached Wantedpages.
    Does not test external URLs, template existence or interwiki destinations.
    """
    wiki = client()
    if title:
        page = wiki.read(title)
        result = wiki.collect(
            {
                "action": "query",
                "generator": "links",
                "pageids": page["pageid"],
                "prop": "info",
                "gpllimit": limit,
            },
            all=all_,
            max_batches=max_batches,
        )
        result["items"] = [
            p
            for batch in result["batches"]
            for p in batch.get("query", {}).get("pages", [])
            if "missing" in p
        ]
        result["source"] = page["title"]
        emit(result)
    else:
        emit(
            wiki.collect(
                {"action": "query", "list": "querypage", "qppage": "Wantedpages", "qplimit": limit},
                all=all_,
                max_batches=max_batches,
            )
        )


def add_report(name, report, description):
    @click.command(name, help=description + " Uses the server's possibly cached/capped QueryPage report.")
    @paging
    def command(limit, all_, max_batches):
        emit(
            client().collect(
                {"action": "query", "list": "querypage", "qppage": report, "qplimit": limit},
                all=all_,
                max_batches=max_batches,
            )
        )

    audit.add_command(command)


add_report("missing-files", "Wantedfiles", "List referenced files that do not exist.")
add_report("broken-redirects", "BrokenRedirects", "List redirects pointing to missing pages.")
add_report("double-redirects", "DoubleRedirects", "List redirects pointing to another redirect.")
add_report("orphaned-pages", "Lonelypages", "List pages without incoming wiki links.")


def add_relation(name, description, params_factory):
    @click.command(name, help=description)
    @click.argument("title")
    @paging
    def command(title, limit, all_, max_batches):
        emit(
            client().collect(
                {"action": "query", **params_factory(title, limit)}, all=all_, max_batches=max_batches
            )
        )

    cli.add_command(command)


add_relation(
    "links", "List outgoing wiki links from TITLE.", lambda t, n: {"prop": "links", "titles": t, "pllimit": n}
)
add_relation(
    "templates",
    "List pages/templates transcluded by TITLE.",
    lambda t, n: {"prop": "templates", "titles": t, "tllimit": n},
)
add_relation(
    "embedded-in",
    "List pages transcluding TITLE (include Template: where applicable).",
    lambda t, n: {"list": "embeddedin", "eititle": t, "eilimit": n},
)
add_relation(
    "backlinks",
    "List pages linking to TITLE.",
    lambda t, n: {"list": "backlinks", "bltitle": t, "bllimit": n},
)
add_relation(
    "categories",
    "List categories containing TITLE.",
    lambda t, n: {"prop": "categories", "titles": t, "cllimit": n},
)
add_relation(
    "category-members",
    "List category members; TITLE must include Category: (or its localized prefix).",
    lambda t, n: {"list": "categorymembers", "cmtitle": t, "cmlimit": n},
)


def change_filters(fn):
    for option, help_text in (
        ("--title", "Restrict to this page title."),
        ("--user", "Restrict to this editor's username."),
        ("--start", "Newest UTC timestamp, e.g. 2026-10-17T18:00:00Z (newest first)."),
        ("--end", "Oldest UTC timestamp, e.g. 2026-10-16T00:00:00Z."),
        ("--namespace", "Namespace IDs separated by '|'."),
    ):
        fn = click.option(option, help=help_text)(fn)
    fn = click.option("--exclude-bots", is_flag=True, help="Exclude changes marked as bot edits.")(fn)
    return fn


def changes(*, unpatrolled=False, **options):
    show = (["!patrolled"] if unpatrolled else []) + (["!bot"] if options["exclude_bots"] else [])
    return client().collect(
        {
            "action": "query",
            "list": "recentchanges",
            "rcnamespace": options["namespace"],
            "rctitle": options["title"],
            "rcuser": options["user"],
            "rcstart": options["start"],
            "rcend": options["end"],
            "rcshow": "|".join(show) or None,
            "rctype": "edit|new" if unpatrolled else None,
            "rclimit": options["limit"],
            "rcprop": "title|ids|timestamp|user|comment|flags|sizes|tags"
            + ("|patrolled" if unpatrolled else ""),
        },
        all=options["all_"],
        max_batches=options["max_batches"],
    )


@cli.command("recent-changes")
@change_filters
@click.option("--unpatrolled", is_flag=True, help="Only unpatrolled edits/new pages; requires patrol rights.")
@paging
def recent_changes(**options):
    """List recent changes, newest first (bounded by the server's retention)."""
    emit(changes(**options))


@cli.group()
def patrol():
    """Review and mark core MediaWiki recent-change patrol (not FlaggedRevs approval).

    Workflow: patrol queue -> patrol show REVID -> guidelines read -> patrol mark.
    Use whoami to check patrol/patrolmarks rights and a Bot Password with the
    patrol grant. Server patrol settings and recent-changes retention apply.
    Mark only revisions you reviewed; there is no automatic bulk acceptance.
    Use undo with --base-revid to revert a bad edit instead of marking it.
    """


@patrol.command("queue")
@change_filters
@paging
def patrol_queue(**options):
    """List unpatrolled edits/new pages with rcid, revid and old_revid for review.

    Example: mw-cli patrol queue --namespace 0 --exclude-bots --all
    Default newest first. --start/--end bound UTC time, --title/--user filter.
    complete describes pagination, not the entire history beyond server retention.
    Requires patrol or patrolmarks rights; permissions/errors are not hidden.
    """
    emit(changes(unpatrolled=True, **options))


@patrol.command("show")
@click.argument("revision", type=click.IntRange(min=1))
def patrol_show(revision):
    """Inspect REVID: author, summary, tags, source and HTML diff against its parent.

    Example: mw-cli patrol show 123
    Creation has no parent: diff=null, creation=true; review the full source.
    Hidden/inaccessible source fails explicitly. This never marks the edit.
    """
    emit(patrolling.inspect_revision(client(), revision))


@patrol.command("mark")
@click.option("--rcid", type=click.IntRange(min=1), help="Recent-changes ID from patrol queue.")
@click.option("--revision", type=click.IntRange(min=1), help="Revision ID you reviewed.")
@click.option("--tag", multiple=True, help="Existing allowed change tag for the patrol log; repeatable.")
@dry_option
def patrol_mark(rcid, revision, tag, dry_run):
    """Mark exactly one reviewed edit as patrolled. Supply --rcid OR --revision.

    Example: mw-cli patrol mark --revision 123
    Requires authentication, patrol grant/right and current guidelines receipt.
    Own edits may be disallowed. Expired/already patrolled edits follow server
    semantics. On outcome-unknown check queue/log before retrying. Core MediaWiki
    has no corresponding unpatrol command. No publication/FlaggedRevs approval.
    """
    require(bool(rcid) != bool(revision), "Supply exactly one of --rcid or --revision.")
    mutation({"action": "patrol", "rcid": rcid, "revid": revision, "tags": "|".join(tag) or None}, dry_run)


@patrol.command("log")
@click.option("--title", help="Filter by page title.")
@click.option("--user", help="Filter by patrolling user (not the original editor).")
@click.option("--start", help="Newest UTC timestamp, newest first.")
@click.option("--end", help="Oldest UTC timestamp.")
@paging
def patrol_log(title, user, start, end, limit, all_, max_batches):
    """Read patrol log events, including automatic patrol where recorded by the server.

    Example: mw-cli patrol log --title Guide --all
    Includes log details/revision IDs when visible. Suppressed details stay hidden.
    """
    emit(
        client().collect(
            {
                "action": "query",
                "list": "logevents",
                "letype": "patrol",
                "letitle": title,
                "leuser": user,
                "lestart": start,
                "leend": end,
                "lelimit": limit,
                "leprop": "ids|title|type|user|timestamp|comment|details",
            },
            all=all_,
            max_batches=max_batches,
        )
    )


@cli.command()
@click.argument("source")
@click.argument("destination")
@click.option("--reason", required=True, help="Move log reason.")
@click.option("--talk", is_flag=True, help="Also move the associated talk page.")
@click.option("--subpages", is_flag=True, help="Also move subpages (requires rights).")
@click.option("--no-redirect", is_flag=True, help="Do not leave a redirect (requires suppressredirect).")
@dry_option
def move(source, destination, reason, talk, subpages, no_redirect, dry_run):
    """Rename SOURCE to DESTINATION. Leaves a redirect unless --no-redirect.

    Example: mw-cli move 'Old title' 'New title' --reason 'Use current terminology'
    """
    mutation(
        {
            "action": "move",
            "from": source,
            "to": destination,
            "reason": reason,
            "movetalk": talk,
            "movesubpages": subpages,
            "noredirect": no_redirect,
        },
        dry_run,
    )


@cli.command()
@click.argument("title")
@click.option("--reason", required=True, help="Deletion log reason; requires delete right.")
@dry_option
def delete(title, reason, dry_run):
    """Delete a page (or File: page and file). Explicit command; no interactive prompt."""
    mutation({"action": "delete", "title": title, "reason": reason}, dry_run)


@cli.command()
@click.argument("title")
@click.option("--reason", required=True, help="Restoration log reason.")
@click.option(
    "--timestamp", multiple=True, help="Restore only these deleted revision timestamps; omit for all."
)
@dry_option
def undelete(title, reason, timestamp, dry_run):
    """Restore deleted page revisions (requires undelete rights)."""
    mutation(
        {"action": "undelete", "title": title, "reason": reason, "timestamps": "|".join(timestamp) or None},
        dry_run,
    )


@cli.command()
@click.argument("title")
@click.option("--revision", type=click.IntRange(min=1), required=True, help="Revision to undo.")
@click.option("--after", type=click.IntRange(min=1), help="Undo a range ending after this revision.")
@click.option(
    "--base-revid", type=click.IntRange(min=1), required=True, help="Current revision you inspected."
)
@click.option("--summary", required=True, help="Reason for undoing the edit(s).")
@dry_option
def undo(title, revision, after, base_revid, summary, dry_run):
    """Undo edits with conflict detection. Example: undo Guide --revision 20 --base-revid 25 --summary 'Revert error'"""
    mutation(
        {
            "action": "edit",
            "title": title,
            "undo": revision,
            "undoafter": after,
            "baserevid": base_revid,
            "nocreate": True,
            "summary": summary,
        },
        dry_run,
    )


@cli.command()
@click.argument("title")
@click.option(
    "--protections",
    required=True,
    help="Pipe-separated levels, e.g. 'edit=sysop|move=sysop'; 'edit=all' unprotects.",
)
@click.option(
    "--expiry", default="infinite", show_default=True, help="Expiry, or matching pipe-separated expiries."
)
@click.option("--reason", required=True, help="Protection log reason; requires protect right.")
@dry_option
def protect(title, protections, expiry, reason, dry_run):
    """Set page protection. Inspect capabilities for effective rights first."""
    mutation(
        {"action": "protect", "title": title, "protections": protections, "expiry": expiry, "reason": reason},
        dry_run,
    )


@cli.command()
@click.argument("title")
@click.option("--unwatch", is_flag=True, help="Remove instead of adding the watch.")
@dry_option
def watch(title, unwatch, dry_run):
    """Add/remove a page on the authenticated account's watchlist."""
    mutation({"action": "watch", "titles": title, "unwatch": unwatch}, dry_run)


@cli.command()
@click.argument("title")
@dry_option
def purge(title, dry_run):
    """Invalidate cached page output. This is a mutation and checks guidelines."""
    mutation({"action": "purge", "titles": title}, dry_run)


@cli.command()
@click.argument("title")
@click.option(
    "--file",
    "source",
    type=click.File("r", encoding="utf-8"),
    help="Preview local wikitext instead of stored page.",
)
@click.option(
    "--raw", is_flag=True, help="Emit rendered HTML only. This does not execute diagram JavaScript."
)
def parse(title, source, raw):
    """Render a page or preview wikitext using the wiki parser without saving.

    Example: mw-cli parse 'Guide' --file guide.wiki --raw
    For Mermaid/draw.io, successful parsing alone does not prove browser rendering.
    """
    params = {"action": "parse", "prop": "text|categories|links|templates|modules"}
    params.update(
        {"title": title, "text": source.read(), "contentmodel": "wikitext"} if source else {"page": title}
    )
    result = client().request(params, post=bool(source))
    if raw:
        click.echo(result["parse"]["text"], nl=False)
    else:
        emit(result)


@cli.command()
@click.argument("from_revision", type=click.IntRange(min=1))
@click.argument("to_revision", type=click.IntRange(min=1))
def diff(from_revision, to_revision):
    """Compare two revision IDs; result contains MediaWiki's HTML diff."""
    emit(
        client().request(
            {"action": "compare", "fromrev": from_revision, "torev": to_revision, "prop": "diff|ids|title"}
        )
    )


@cli.group("file")
def file_group():
    """Inspect, download and chunk-upload binary files. Delete via delete 'File:Name'."""


@file_group.command("info")
@click.argument("title")
def file_info(title):
    """Read URL, SHA-1, size, MIME and upload metadata. File: prefix is optional."""
    emit(files.info(client(), title))


@file_group.command("list")
@click.option("--prefix", default="", help="Filename prefix without File:; underscores follow server rules.")
@paging
def file_list(prefix, limit, all_, max_batches):
    """List local wiki files alphabetically with URL, MIME, size, SHA-1 and uploader.

    Example: mw-cli file list --prefix Architecture --all
    Uses allimages, not an inventory of shared/foreign repositories or old versions.
    """
    emit(
        client().collect(
            {
                "action": "query",
                "list": "allimages",
                "aiprefix": prefix,
                "ailimit": limit,
                "aiprop": "timestamp|user|url|size|sha1|mime",
            },
            all=all_,
            max_batches=max_batches,
        )
    )


@file_group.command("download")
@click.argument("title")
@click.option(
    "--output",
    required=True,
    type=click.Path(path_type=Path),
    help="Explicit destination path; never inferred from remote names.",
)
@click.option(
    "--overwrite", is_flag=True, help="Replace an existing local file after download integrity checks."
)
@click.option(
    "--max-bytes",
    type=click.IntRange(min=1),
    default=1024**3,
    show_default=True,
    help="Download size ceiling.",
)
def file_download(title, output, overwrite, max_bytes):
    """Stream a file to disk; verify size/SHA-1 and publish atomically.

    Same-origin private files use the login session. Cross-origin storage/CDNs
    receive no wiki cookies. HTTPS downgrades are rejected. Partial downloads
    are removed. Example: mw-cli file download 'File:Diagram.svg' --output diagram.svg
    """
    emit(files.download(client(), title, output, overwrite=overwrite, max_bytes=max_bytes))


@file_group.command("upload")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--filename", help="Destination filename without File: prefix; defaults to local basename.")
@click.option("--comment", required=True, help="Upload log comment.")
@click.option(
    "--description-file",
    type=click.File("r", encoding="utf-8"),
    help="Initial File: page wikitext (licensing, source, categories).",
)
@click.option(
    "--overwrite",
    is_flag=True,
    help="Permit a new version of an existing wiki file; server warnings still stop upload.",
)
@click.option(
    "--ignore-warnings",
    is_flag=True,
    help="Explicitly accept API upload warnings, including duplicates/overwrite. Inspect first.",
)
@click.option(
    "--chunk-size",
    type=click.IntRange(1, 64 * 1024**2),
    default=5 * 1024**2,
    show_default=True,
    help="Bytes per multipart chunk; bounds memory use.",
)
@dry_option
def file_upload(source, filename, comment, description_file, overwrite, ignore_warnings, chunk_size, dry_run):
    """Upload a local file in bounded chunks and commit it. Checks guidelines.

    Requires upload (and reupload for replacement) Bot Password grants, enabled
    uploads and an allowed extension/MIME type on the wiki. No server URL fetch.
    Failed uploads can leave temporary stashes, expired by MediaWiki itself.

    Example: mw-cli file upload diagram.svg --comment 'Update architecture' --description-file license.wiki
    """
    emit(
        files.upload(
            client(),
            source,
            filename=filename,
            comment=comment,
            description=description_file.read() if description_file else None,
            overwrite=overwrite,
            ignore_warnings=ignore_warnings,
            chunk_size=chunk_size,
            dry_run=dry_run,
        )
    )


@cli.group()
def smw():
    """Query Semantic MediaWiki. Discover installation with capabilities.

    Write semantic annotations through write using [[Property::Value]] or the
    wiki's templates. Property types and templates come from that wiki's rules.
    Indexing is asynchronous on some installations; immediate queries may lag.
    """


@smw.command("ask")
@click.argument("query")
@click.option("--all", "all_", is_flag=True, help="Follow SMW query-continue-offset.")
@click.option("--max-batches", type=click.IntRange(1, 10000), default=100, show_default=True)
def smw_ask(query, all_, max_batches):
    """Execute #ask syntax; use |limit=N in QUERY to set the batch size.

    Example: mw-cli smw ask '[[Category:Servers]]|?IP address|?Owner|limit=50' --all
    Results retain SMW datatypes/printouts. complete=false means more data remains.
    """
    emit(semantic.ask(client(), query, all=all_, max_batches=max_batches))


@smw.command("askargs")
@click.option(
    "--conditions", required=True, help="SMW conditions without outer brackets, e.g. 'Category:Servers'."
)
@click.option("--printouts", default="", help="Property names separated by '|', e.g. 'IP address|Owner'.")
@click.option(
    "--parameters",
    default="limit=50",
    show_default=True,
    help="Query options separated by '|', including offset for continuation.",
)
def smw_askargs(conditions, printouts, parameters):
    """Execute one structured SMW query; output includes any continuation offset.

    Example: mw-cli smw askargs --conditions 'Category:Servers' --printouts 'Owner' --parameters 'limit=50|offset=0'
    """
    emit(
        client().request(
            {"action": "askargs", "conditions": conditions, "printouts": printouts, "parameters": parameters}
        )
    )


@smw.command("browse")
@click.argument("subject")
@click.option(
    "--namespace", type=int, default=0, show_default=True, help="Default namespace for an unprefixed subject."
)
@click.option("--subobject", default="", help="Optional SMW subobject identifier.")
def smw_browse(subject, namespace, subobject):
    """Read semantic properties of SUBJECT via smwbrowse. Names are wiki page titles."""
    emit(
        client().request(
            {
                "action": "smwbrowse",
                "browse": "subject",
                "params": json.dumps({"subject": subject, "ns": namespace, "subobject": subobject}),
            }
        )
    )


@smw.command("info")
def smw_info():
    """Read semantic store statistics (property/value/error counts)."""
    emit(client().request({"action": "smwinfo", "info": "proppagecount|propcount|errorcount"}))


@cli.command("api")
@click.argument("action")
@click.option("--param", multiple=True, help="Repeat KEY=VALUE; values may contain '='. No tokens/passwords.")
@click.option(
    "--params-file",
    type=click.File("r", encoding="utf-8"),
    help="JSON object of module parameters; '-' reads stdin.",
)
@click.option(
    "--write",
    "write_",
    is_flag=True,
    help="Explicit mutation mode: checks guidelines, authenticates, POSTs with the action's token.",
)
@dry_option
@click.option("--all", "all_", is_flag=True, help="Follow standard action=query continuation; read-only.")
@click.option(
    "--max-batches",
    type=click.IntRange(1, 10000),
    default=100,
    show_default=True,
    help="Hard stop for --all. Check complete/continuation in the result.",
)
def api(action, param, params_file, write_, dry_run, all_, max_batches):
    """Escape hatch for additional API modules. JSON responses are preserved.

    Read mode allows query, parse, compare, expandtemplates, paraminfo, help,
    ask, askargs, smwbrowse and smwinfo. Other modules require --write; never
    bypass the guidelines gate. Known core actions select their token type;
    mutating extensions must accept a CSRF token.
    Use paraminfo to discover installed API parameters instead of guessing.

    \b
    mw-cli api paraminfo --param modules=edit
    mw-cli api query --param list=allimages --param ailimit=50
    mw-cli api edit --write --params-file edit.json

    Raw edit also requires baserevid or createonly. For binary uploads use file
    upload. No login/logout/token overrides here. Dry-run is for --write only.
    --all is only for read-mode action=query: returns batches/complete/continuation.
    Without --all the original response shape is preserved. SMW uses smw ask --all.
    """
    params = json.load(params_file) if params_file else {}
    require(isinstance(params, dict), "--params-file must contain a JSON object.")
    for item in param:
        key, separator, value = item.partition("=")
        require(separator and key, "--param must be KEY=VALUE.")
        params[key] = value
    forbidden = {
        "action",
        "format",
        "formatversion",
        "assert",
        "assertuser",
        "token",
        "lgtoken",
        "lgpassword",
        "lgname",
    }
    require(
        not forbidden.intersection(params), "Reserved authentication/format parameters cannot be overridden."
    )
    require(
        action not in ("login", "clientlogin", "logout", "upload"),
        "Use the dedicated authentication/file workflow.",
    )
    require(not dry_run or write_, "--dry-run requires --write.")
    require(not all_ or (action == "query" and not write_), "--all requires read-mode action=query.")
    require(
        all(isinstance(v, (str, int, float, bool)) or v is None for v in params.values()),
        "API parameters must be scalar values.",
    )
    params = {"action": action, **params}
    if write_:
        if action == "edit":
            base = params.get("baserevid")
            create = params.get("createonly")
            require(
                bool(base) != (create is not None and create is not False),
                "Raw edit needs exactly one of baserevid or createonly.",
            )
            if base:
                require(str(base).isdigit() and int(base) > 0, "baserevid must be a positive integer.")
                params["nocreate"] = True
        mutation(params, dry_run)
    else:
        require(
            action
            in {
                "query",
                "parse",
                "compare",
                "expandtemplates",
                "paraminfo",
                "help",
                "ask",
                "askargs",
                "smwbrowse",
                "smwinfo",
            },
            "Unknown/read-unsafe action. Use --write for mutating modules.",
        )
        emit(
            client().collect(params, all=True, max_batches=max_batches) if all_ else client().request(params)
        )


admin.register(cli, lambda: client(), emit, mutation, paging, dry_option)


def main():
    import sys

    # Stable UTF-8 even when an agent redirects stdout on a legacy Windows locale.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    cli()

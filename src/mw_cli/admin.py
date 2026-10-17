"""Moderator command registration; extension integration uses public HTTP APIs only."""

import re

import click

from .errors import WikiError, require


def module_info(wiki, name):
    data = wiki.request({"action": "paraminfo", "modules": name})
    modules = data.get("paraminfo", {}).get("modules", [])
    if not modules or "missing" in modules[0]:
        raise WikiError("unsupported-module", f"This wiki does not expose API module {name}.")
    return modules[0]


def register(cli, get_client, emit, mutation, paging, dry_option):
    def dates(fn):
        fn = click.option("--start", help="Newest UTC timestamp (newest first).")(fn)
        return click.option("--end", help="Oldest UTC timestamp.")(fn)

    @cli.command("contributions")
    @click.argument("user")
    @click.option("--namespace", help="Namespace IDs separated by '|'.")
    @dates
    @paging
    def contributions(user, namespace, start, end, limit, all_, max_batches):
        """List USER's edits, revision/parent IDs, summaries, sizes and tags.

        Example: mw-cli contributions Example --namespace 0 --all
        Includes visible revision history beyond recent-changes retention. Hidden
        and deleted edits are not exposed. Use diff/patrol show to inspect a revision.
        """
        emit(
            get_client().collect(
                {
                    "action": "query",
                    "list": "usercontribs",
                    "ucuser": user,
                    "ucnamespace": namespace,
                    "ucstart": start,
                    "ucend": end,
                    "uclimit": limit,
                    "ucprop": "ids|title|timestamp|comment|size|sizediff|flags|tags",
                },
                all=all_,
                max_batches=max_batches,
            )
        )

    @cli.command("logs")
    @click.option("--type", "kind", help="Log type, e.g. delete, block, move, protect, patrol.")
    @click.option("--title", help="Affected page, e.g. User:Example for blocks.")
    @click.option("--user", help="Actor who performed the action, not its target.")
    @dates
    @paging
    def logs(kind, title, user, start, end, limit, all_, max_batches):
        """Read the general action log with filters and bounded pagination.

        Example: mw-cli logs --type block --title User:Example --all
        Suppression and access rights are respected. Extra types depend on extensions.
        """
        emit(
            get_client().collect(
                {
                    "action": "query",
                    "list": "logevents",
                    "letype": kind,
                    "letitle": title,
                    "leuser": user,
                    "lestart": start,
                    "leend": end,
                    "lelimit": limit,
                    "leprop": "ids|title|type|user|timestamp|comment|details|tags",
                },
                all=all_,
                max_batches=max_batches,
            )
        )

    @cli.command("blocks")
    @click.option("--user", help="Exact username/IP/range (not the blocking actor).")
    @click.option("--ip", help="Find blocks applying to this IP, including ranges. Exclusive with --user.")
    @paging
    def blocks(user, ip, limit, all_, max_batches):
        """List active blocks, IDs, expiry, reason, flags and partial restrictions.

        Example: mw-cli blocks --user Example --all
        Use logs --type block for historical events; this is current block state.
        """
        require(not (user and ip), "Choose --user or --ip, not both.")
        emit(
            get_client().collect(
                {
                    "action": "query",
                    "list": "blocks",
                    "bkusers": user,
                    "bkip": ip,
                    "bklimit": limit,
                    "bkprop": "id|user|by|timestamp|expiry|reason|flags|restrictions",
                },
                all=all_,
                max_batches=max_batches,
            )
        )

    @cli.command("block")
    @click.argument("user")
    @click.option(
        "--expiry", required=True, help="Explicit duration/UTC expiry, e.g. '2 hours' or 'infinite'."
    )
    @click.option("--reason", required=True, help="Public block log reason.")
    @click.option(
        "--reblock", is_flag=True, help="Explicitly replace an existing single block; inspect first."
    )
    @click.option("--page", multiple=True, help="Restrict to existing page(s); enables partial block.")
    @click.option(
        "--namespace", multiple=True, type=click.IntRange(min=0), help="Restrict to namespace ID(s)."
    )
    @click.option(
        "--anon-only", is_flag=True, help="For IP blocks, allow logged-in users (server semantics)."
    )
    @click.option("--no-create", is_flag=True, help="Prevent account creation.")
    @click.option("--no-email", is_flag=True, help="Prevent wiki email; requires blockemail.")
    @click.option(
        "--autoblock", is_flag=True, help="Also block account IPs. Off unless explicitly requested."
    )
    @click.option("--allow-talk", is_flag=True, help="Allow own talk-page editing if server policy permits.")
    @dry_option
    def block(
        user,
        expiry,
        reason,
        reblock,
        page,
        namespace,
        anon_only,
        no_create,
        no_email,
        autoblock,
        allow_talk,
        dry_run,
    ):
        """Block a user/IP/range with explicit expiry/reason and guidelines guard.

        Example: mw-cli block Example --expiry '2 hours' --reason 'Repeated vandalism' --dry-run
        No automatic IP autoblock. Without --page/--namespace the block is sitewide.
        Requires block right and Bot Password blockusers grant. Existing blocks
        are not silently overwritten. --reblock may fail for multiple blocks;
        inspect blocks and use version-specific API parameters if necessary.
        """
        require(
            bool(user.strip()) and bool(expiry.strip()) and bool(reason.strip()),
            "User, expiry and reason must not be empty.",
        )
        require(all(p.strip() and "|" not in p for p in page), "Pass each nonempty page with its own --page.")
        mutation(
            {
                "action": "block",
                "user": user,
                "expiry": expiry,
                "reason": reason,
                "reblock": reblock,
                "partial": bool(page or namespace),
                "pagerestrictions": "|".join(page) or None,
                "namespacerestrictions": "|".join(map(str, namespace)) or None,
                "anononly": anon_only,
                "nocreate": no_create,
                "noemail": no_email,
                "autoblock": autoblock,
                "allowusertalk": allow_talk,
            },
            dry_run,
        )

    @cli.command("unblock")
    @click.option("--user", help="User/IP/range to unblock; use --id to target one specific block.")
    @click.option("--id", "block_id", type=click.IntRange(min=1), help="Block ID returned by blocks.")
    @click.option("--reason", required=True, help="Public unblock log reason.")
    @dry_option
    def unblock(user, block_id, reason, dry_run):
        """Remove a block by exactly one of --user or --id, with guidelines guard.

        Example: mw-cli unblock --id 123 --reason 'Issue resolved'
        User-based behavior for multiple blocks depends on the MediaWiki version;
        prefer --id. Requires block right and Bot Password blockusers grant.
        """
        require(bool(user) != bool(block_id), "Supply exactly one of --user or --id.")
        require(bool(reason.strip()) and (user is None or bool(user.strip())), "User/reason cannot be empty.")
        mutation({"action": "unblock", "user": user, "id": block_id, "reason": reason}, dry_run)

    @cli.group()
    def moderation():
        """Extension:Moderation actions by pending edit ID (not core patrol).

        Requires the extension and moderation permission. Obtain modid from the
        edit's queued response or Special:Moderation: the supported extension API
        has no general queue-list endpoint. No automatic approve-all/reject-all.
        Inspect with show before approving/rejecting; these actions publish/reject edits.
        """

    @moderation.command("show")
    @click.argument("modid", type=click.IntRange(min=1))
    def moderation_show(modid):
        """Read one pending edit via the extension's authenticated POST show API.

        Example: mw-cli moderation show 123
        Requires login/CSRF token, but does not approve/reject and needs no rules receipt.
        Output is the extension's raw result; do not execute returned HTML/scripts.
        """
        wiki = get_client()
        module_info(wiki, "moderation")
        emit(
            wiki.request(
                {
                    "action": "moderation",
                    "modaction": "show",
                    "modid": modid,
                    "token": wiki.token(),
                    "assert": "user",
                },
                post=True,
            )
        )

    def add_moderation_action(name):
        @moderation.command(name)
        @click.argument("modid", type=click.IntRange(min=1))
        @dry_option
        def command(modid, dry_run):
            """Apply this action to exactly one pending edit ID. Auth/rules required.

            Example: mw-cli moderation approve 123 --dry-run
            This API does not accept a free-form reason. Review with moderation show.
            """
            module_info(get_client(), "moderation")
            mutation({"action": "moderation", "modaction": name, "modid": modid}, dry_run)

    add_moderation_action("approve")
    add_moderation_action("reject")

    @cli.group()
    def flagged():
        """FlaggedRevs review/approval, separate from core patrol and Moderation.

        Discover installed flags with config and api paraminfo --param modules=review.
        Uses the extension's public API; server version/settings/rights may differ.
        Review full source/diff via patrol show REVID before applying approval.
        """

    @flagged.command("config")
    def flagged_config():
        """Read installed flag names/levels through action=flagconfig."""
        module_info(get_client(), "flagconfig")
        emit(get_client().request({"action": "flagconfig"}))

    @flagged.command("pending")
    @click.option("--new", "new_", is_flag=True, help="Never-reviewed pages instead of outdated reviews.")
    @click.option("--namespace", type=click.IntRange(min=0), default=0, show_default=True)
    @paging
    def flagged_pending(new_, namespace, limit, all_, max_batches):
        """List pages needing review. --new requires the unreviewedpages API module.

        Example: mw-cli flagged pending --namespace 0 --all
        Default uses oldreviewedpages; --new uses unreviewedpages when available.
        A missing module is an error, never an apparently empty queue.
        """
        module, prefix = ("unreviewedpages", "ur") if new_ else ("oldreviewedpages", "or")
        module_info(get_client(), "query+" + module)
        emit(
            get_client().collect(
                {"action": "query", "list": module, prefix + "namespace": namespace, prefix + "limit": limit},
                all=all_,
                max_batches=max_batches,
            )
        )

    @flagged.command("review")
    @click.argument("revision", type=click.IntRange(min=1))
    @click.option("--flag", multiple=True, help="Flag assignment NAME=LEVEL, e.g. accuracy=1. Repeatable.")
    @click.option("--comment", required=True, help="Review log comment.")
    @click.option("--unapprove", is_flag=True, help="Withdraw review; cannot combine with --flag.")
    @dry_option
    def flagged_review(revision, flag, comment, unapprove, dry_run):
        """Approve/grade one revision, or withdraw its review with --unapprove.

        Example: mw-cli flagged review 123 --flag accuracy=1 --comment 'Checked sources'
        Omit flags only for installations with binary/default approval; inspect config
        and paraminfo first. Unknown flags/levels are rejected before writing.
        Requires review rights and applicable Bot Password grants plus guidelines.
        """
        require(not (unapprove and flag), "--unapprove cannot be combined with --flag.")
        values = {}
        for assignment in flag:
            name, sep, value = assignment.partition("=")
            require(
                sep and re.fullmatch(r"[A-Za-z0-9_]+", name) and value.isascii() and value.isdigit(),
                "Flags must be NAME=nonnegative-integer.",
            )
            require("flag_" + name not in values, "Duplicate flag name.")
            values["flag_" + name] = value
        module = module_info(get_client(), "review")
        params = {p["name"]: p for p in module.get("parameters", [])}
        for key, value in values.items():
            require(key in params, f"Flag not supported by this wiki: {key}")
            allowed = params[key].get("type")
            require(not isinstance(allowed, list) or value in list(map(str, allowed)), "Invalid flag level.")
        mutation(
            {"action": "review", "revid": revision, "comment": comment, "unapprove": unapprove, **values},
            dry_run,
        )

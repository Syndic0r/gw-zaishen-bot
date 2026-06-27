"""Builds the daily message text: the four quests, each with its own sign-up roster."""

import discord

import storage
import zaishen


def content(guild_id=None):
    """The daily message body — header + each quest (wiki-linked) with who's up for it.

    With a `guild_id`, the per-server roster + the sign-up footer are shown (the pinned message, and
    `/zaishen` used in a server). Without one (e.g. `/zaishen` in a DM), only the quests are shown."""
    day = zaishen.zaishen_day()
    zday = day.isoformat()
    reset_epoch = int(zaishen.next_reset().timestamp())
    signs = storage.signups(guild_id, zday) if guild_id is not None else {}
    igns = storage.igns_for({u for ups in signs.values() for u in ups})

    def who(uid):
        name = igns.get(uid)
        # escape the user-supplied IGN so markdown in it can't distort the roster; the message is
        # sent with allowed_mentions=none, so any mention-like text in it never pings either.
        return f"<@{uid}> ({discord.utils.escape_markdown(name)})" if name else f"<@{uid}>"

    lines = [
        "# ⚔️ Guild Wars — Zaishen Dailies",
        f"📅 **<t:{reset_epoch - 86400}:D>** · next reset <t:{reset_epoch}:R>",
        "",
    ]
    for qt, emoji, label, name in zaishen.all_quests(day):
        lines.append(f"{emoji} **{label}:** [{name}]({zaishen.wiki_url(name)})")
        if guild_id is None:
            continue
        ups = signs.get(qt, [])
        if ups:
            lines.append(f"   └ up ({len(ups)}): " + ", ".join(who(u) for u in ups))
        else:
            lines.append("   └ _nobody yet_")

    if guild_id is not None:
        lines.append("")
        lines.append("-# Tap a quest to sign up • ✅ All for everything • 🧹 to sign off all.")
    return "\n".join(lines)
